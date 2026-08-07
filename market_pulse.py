"""盘中涨跌速度监控 — 回答「我的票在跌，是独跌还是全市场同跌」。

设计文档：docs/market_pulse/spec/2026-08-07-market_pulse-spec.md

只描述已经发生的涨跌，不做方向预测。
"""
import datetime
import json
import os
import statistics as st
import unicodedata
import zoneinfo

import intraday_guide as ig

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(BASE_DIR, "pulse_store.jsonl")

POOL_CODES = tuple(ig.POOL)
IDX_CODES = ("sz399006", "sh000001", "sh000688")
IDX_NAMES = {"sz399006": "创业板指", "sh000001": "上证", "sh000688": "科创50"}

# 长期持仓，见 ~/.claude 记忆 stock-holdings。长鑫科技不在 38 只池内，
# 它只在「相对强弱」出数字，速度栏是 —。
HOLD_CODES = ("sz300308", "sz301526", "sh688825")
HOLD_NAMES = {"sh688825": "长鑫科技"}

WINDOWS = (15, 60, 300)      # 速度窗口，单位秒
MIN_VALID = 20               # 池内有效票少于这个数就不出判定
VERDICT_RATIO = 0.60         # 判定的涨跌占比门槛，闭区间
STALE_SEC = 60               # 最后一条快照超过这么久，视为采集未运行


def merge_codes(watch, pool=POOL_CODES, idx=IDX_CODES):
    """把自选、池子、指数合成一份去重代码表，自选顺序保留在最前。

    自选顺序要保留，因为悬浮窗按这个顺序渲染。
    """
    out = list(watch)
    seen = set(out)
    for code in tuple(pool) + tuple(idx):
        if code not in seen:
            out.append(code)
            seen.add(code)
    return out


def split_result(quotes, watch, pool=POOL_CODES):
    """把一次批量返回按用途分拣成 (自选部分, 池子部分)。

    重叠的票同时出现在两边，指向同一个对象。请求里有、返回里没有的代码
    两边都不出现——不塞占位值，让上层能看出到底缺了什么。
    """
    watch_part = {c: quotes[c] for c in watch if c in quotes}
    pool_part = {c: quotes[c] for c in pool if c in quotes}
    return watch_part, pool_part


SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))


def in_session(hhmm):
    """是不是连续竞价时段。入参 "HH:MM" 或 "HH:MM:SS"。

    集合竞价（9:15-9:25）返回 False：那时的价格是虚拟撮合价，
    和连续竞价不是同一个东西，混进速度序列会产生假信号。
    格式不合法抛 ValueError，不静默返回 False——静默会让采集悄悄停掉。
    """
    parts = hhmm.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"时间格式不对: {hhmm!r}")
    try:
        minutes = int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        raise ValueError(f"时间格式不对: {hhmm!r}") from None
    return any(lo <= minutes <= hi for lo, hi in SESSIONS)


def speed(r_now, r_past):
    """速度 = 涨跌幅之差，单位百分点(pp)。

    用涨跌幅之差而不是价格变化率，是为了让 977 元的中际旭创和 38 元的
    国际复材能放进同一张表比较。

    任一端缺数据返回 None，不返回 0——0 表示「没动」，和「不知道」是两回事。
    """
    if r_now is None or r_past is None:
        return None
    return r_now - r_past


def aggregate(values):
    """一组速度或涨跌幅取中位数，返回 (中位数, 有效数)。

    用中位数不用平均数：池子里有涨停跌停的极端票，平均数会被拉偏。
    有效数一起返回，让上层能判断样本够不够（见 MIN_VALID）。
    """
    valid = [v for v in values if v is not None]
    if not valid:
        return None, 0
    return st.median(valid), len(valid)


def breadth(rs_now, rs_past=None):
    """涨跌家数与翻向数。

    rs_now / rs_past 形如 {代码: 涨跌幅%}，值为 None 表示停牌，直接剔除。
    平盘（r == 0）不算翻向——平盘不是一个方向。
    """
    up = down = flat = 0
    for r in rs_now.values():
        if r is None:
            continue
        if r > 0:
            up += 1
        elif r < 0:
            down += 1
        else:
            flat += 1
    flip_down = flip_up = 0
    if rs_past:
        for code, now in rs_now.items():
            past = rs_past.get(code)
            if now is None or past is None or now == 0 or past == 0:
                continue
            if past > 0 > now:
                flip_down += 1
            elif past < 0 < now:
                flip_up += 1
    return {"up": up, "down": down, "flat": flat, "valid": up + down + flat,
            "flip_down": flip_down, "flip_up": flip_up}


def verdict(br, r_stock):
    """回答「是我独跌还是大家都跌」，返回 (判定, 说明)。

    只用计数和比例，不设需要标定的速度阈值——15 秒粒度的历史分布还没积累出来，
    在标定之前给「跌得快/慢」这类定性词是没有依据的。

    比例的分母是有效票数不是固定的 38；正好等于 VERDICT_RATIO 算命中。
    有效票少于 MIN_VALID 时返回 (None, 原因)。
    """
    valid = br["valid"]
    if valid < MIN_VALID:
        return None, f"样本不足（仅 {valid} 只有效，需 {MIN_VALID} 只）"
    up_ratio = br["up"] / valid
    down_ratio = br["down"] / valid
    note = (f"池内 {br['up']}/{valid} 上涨（{up_ratio * 100:.0f}%）、"
            f"{br['down']}/{valid} 下跌（{down_ratio * 100:.0f}%）")
    if r_stock is None or r_stock == 0:
        return "分化", note
    if up_ratio >= VERDICT_RATIO:
        return ("同涨" if r_stock > 0 else "独跌"), note
    if down_ratio >= VERDICT_RATIO and r_stock < 0:
        return "同跌", note
    return "分化", note


def excess(r_stock, r_bench):
    """超额 = 个股涨跌幅 − 基准涨跌幅，单位百分点(pp)。"""
    if r_stock is None or r_bench is None:
        return None
    return r_stock - r_bench


def rank(r_stock, all_rs):
    """池内名次，返回 (名次, 有效数)。

    名次 = 涨跌幅严格大于你的票数 + 1。并列给相同名次，之后跳号
    （[5.0, 3.0, 3.0, 1.0] 的名次是 1、2、2、4）。
    分母是有效票数，停牌票（None）不计入。

    超额单独看没有信息量（+0.69pp 是强是弱看不出来），配上名次才有参照点。
    """
    valid = [r for r in all_rs if r is not None]
    if r_stock is None or not valid:
        return None, len(valid)
    return sum(1 for r in valid if r > r_stock) + 1, len(valid)


TS_FMT = "%Y-%m-%d %H:%M:%S"


def parse_ts(s):
    """解析落盘里的时间戳 "2026-08-07 13:24:15"。格式不对抛 ValueError。"""
    return datetime.datetime.strptime(s, TS_FMT)


def window_tolerance(window_sec):
    """窗口取值的允许偏差 = 窗口长度的三分之一。

    采样点不会正好落在 t − w 上，所以要给一个容差。取三分之一是因为
    3 秒采样下，15 秒窗口的容差 5 秒刚好覆盖一到两个采样间隔。
    """
    return window_sec / 3.0


def pick_snapshot(store, target, tol_sec):
    """取时间戳离 target 最近、且偏差不超过 tol_sec 的那条快照。

    偏差超限返回 None——宁可在面板上标注不可用，也不拿一个口径不同的数去凑。
    """
    best, best_gap = None, None
    for snap in store:
        gap = abs((parse_ts(snap["t"]) - target).total_seconds())
        if gap > tol_sec:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = snap, gap
    return best


def append_store(snap, path=STORE):
    """把一条快照追加落盘，返回是否真的写了。

    非交易时段直接跳过：午休和盘后行情不变，写进去只是一堆重复记录，
    还会污染速度窗口的取值。
    """
    if not in_session(snap["t"].split(" ")[-1]):
        return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return True


def load_store(seconds, now=None, path=STORE):
    """读回最近 seconds 秒的快照，按时间升序。

    损坏行跳过继续读——一行 JSON 写坏不该让整个文件报废。
    文件不存在返回空列表。
    """
    if not os.path.exists(path):
        return []
    now = now or now_bj()
    cutoff = now - datetime.timedelta(seconds=seconds)
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
                ts = parse_ts(snap["t"])
            except (ValueError, KeyError, TypeError):
                continue
            if ts >= cutoff:
                out.append(snap)
    out.sort(key=lambda s: s["t"])
    return out


def rotate_store(today, path=STORE):
    """最后一条不是今天就清空，返回是否清空了。

    昨天的价格接到今天的序列上，会算出一个跨夜跳空的假速度。
    """
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as fh:
        lines = [x for x in fh if x.strip()]
    if not lines:
        return False
    try:
        last_day = json.loads(lines[-1])["t"].split(" ")[0]
    except (ValueError, KeyError, IndexError):
        last_day = None
    if last_day == today:
        return False
    open(path, "w", encoding="utf-8").close()
    return True


def store_status(store, now):
    """判断落盘数据能不能用，返回 (状态码, 说明)。

    三种状态分别对应不同的面板文案，都不给数字、也不用别的数据源凑。
    """
    if not store:
        return "not_running", "不可用（悬浮窗未启动）"
    age = (now - parse_ts(store[-1]["t"])).total_seconds()
    if age > STALE_SEC:
        return "not_running", f"不可用（悬浮窗未启动，最后一条在 {age:.0f} 秒前）"
    span = (now - parse_ts(store[0]["t"])).total_seconds()
    longest = max(WINDOWS)
    if span < longest:
        return "warming_up", f"不可用（已采集 {span:.0f} 秒，需 {longest} 秒）"
    return "ok", ""


def display_width(s):
    """终端显示宽度：全角/宽字符计 2，其余计 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad_l(s, width):
    """按显示宽度左对齐补空格（中文计 2）。超长不截断——截断会把股票名切掉。"""
    return s + " " * max(0, width - display_width(s))


def _pad_r(s, width):
    """按显示宽度右对齐补空格。"""
    return " " * max(0, width - display_width(s)) + s


WINDOW_LABELS = {15: "15秒", 60: "1分钟", 300: "5分钟"}


def _fmt_speed(v, dash=False):
    """速度单元格。

    两种 None 含义不同，必须分开显示：
      dash=True  —— 这只票结构性地没有数据（池外持仓票不落盘），显示「—」
      dash=False —— 有数据但这次取不到（采集未启动、窗口无快照），显示「不可用」
    混成一种会让面板自相矛盾：同一只长鑫科技，速度栏说「不可用」暗示采集出了问题，
    排名栏却说「不在池内」是结构性的。
    """
    if v is None:
        return "—" if dash else "不可用"
    return f"{v:+.2f}"


def render_panel(state):
    """命令行完整面板。state 的结构见 test_market_pulse._state()。

    所有列都用 _pad_l / _pad_r 按显示宽度对齐，不能用 ljust/rjust——
    那两个按码点补齐，而赛道名的中文字数差很多（MLCC(2只) 1 个中文、
    AI算力芯片(3只) 5 个），按码点补会让右缘参差。
    """
    lines = [f"【市场脉搏 {state['ts']}】采样 3 秒 · 池内有效 "
             f"{state['valid']}/{state['total']}", ""]

    lines.append("【速度】单位 pp（该窗口内涨跌幅的变化量）")
    lines.append("  " + _pad_l("", 16)
                 + "".join(_pad_r(WINDOW_LABELS[w], 9) for w in WINDOWS))
    for row in state["rows"]:
        dash = row.get("dash", False)
        lines.append("  " + _pad_l(row["name"], 16)
                     + "".join(_pad_r(_fmt_speed(v, dash), 9)
                               for v in row["speeds"]))
    if state["status"][0] != "ok":
        lines.append(f"  {state['status'][1]}")
    lines.append("")

    br = state["breadth"]
    lines.append("【宽度】")
    lines.append(f"  池内 上涨 {br['up']} / 平盘 {br['flat']} / 下跌 {br['down']}")
    lines.append(f"  最近 1 分钟翻向：涨转跌 {br['flip_down']} 只，"
                 f"跌转涨 {br['flip_up']} 只")
    lines.append("")

    lines.append("【相对强弱】")
    for h in state["holdings"]:
        place, total = h["rank"]
        if place is None:
            pos = "—（不在池内）" if not h.get("in_pool", True) else "—"
        else:
            pos = f"{place}/{total}（前 {place / total * 100:.0f}%）"
        exc = "—" if h["excess"] is None else f"{h['excess']:+.2f}pp"
        r_txt = "—" if h["r"] is None else f"{h['r']:+.2f}%"
        lines.append("  " + _pad_l(h["name"], 10) + _pad_r(r_txt, 9)
                     + "  超额(vs池) " + _pad_r(exc, 9) + "  排名 " + pos)
    lines.append("")

    got, why = state["verdict"]
    lines.append(f"【判定】{why}" if got is None else f"【判定】{got} —— {why}")
    if state.get("dropped"):
        lines.append(f"  跳过 {state['dropped']} 行损坏记录")
    return "\n".join(lines)


def render_strip(state):
    """悬浮窗单行文案，显示宽度不超过 40。

    速度三窗放不进一行，只在命令行面板出现。超限时优先砍池子涨跌幅，
    保留判定和排名——那两个才是「要不要紧」的直接答案。
    """
    got, _ = state["verdict"]
    if got is None:
        return "样本不足" if state["status"][0] == "ok" else "行情不可用"
    br = state["breadth"]
    h = state["holdings"][0] if state["holdings"] else None
    if h is None:
        return f"池{state['pool_median']:+.2f}% {br['up']}/{br['down']} {got}"
    place, total = h["rank"]
    tail = (f"你{h['excess']:+.2f}pp {place}/{total} {got}"
            if h["excess"] is not None and place else f"你 — {got}")
    full = f"池{state['pool_median']:+.2f}% {br['up']}/{br['down']}  {tail}"
    return full if display_width(full) <= 40 else tail


def parse_pool_quotes(raw_quotes):
    """把 stock_watch.fetch_quotes 的返回转成 {代码: {"r": 涨跌幅%, "px": 现价}}。

    入参的字段名是 current / prev_close，那是 stock_watch.parse_sina_response
    的输出（stock_watch.py:75-78），只在 len(fields) > 9 时才附上；报文太短
    （停牌、未开盘）时这两个键不存在，get 返回 None 会被下面剔除。
    输出侧用 px 这个键名，那是本模块自己的形状。

    这里从原始现价与昨收重算涨跌幅，不用 stock_watch 现成的 change_pct——
    后者四舍五入到两位，速度是两个涨跌幅之差，精度损失会被放大。

    停牌（现价为 0）和昨收为 0 的票直接剔除，不列名、不提示。
    """
    out = {}
    for q in raw_quotes:
        if not q.get("ok"):
            continue
        px, pc = q.get("current"), q.get("prev_close")
        if not px or not pc:
            continue
        out[q["code"]] = {"r": (px - pc) / pc * 100, "px": px}
    return out


def _speeds_for(store, now, key_fn):
    """对每个窗口算一次速度。key_fn(snapshot) 返回该快照对应的涨跌幅。

    key_fn 返回 None（该票没落盘，例如池外的长鑫科技）时，速度也是 None，
    面板渲染成「—」。
    """
    out = []
    now_snap = store[-1] if store else None
    for w in WINDOWS:
        if now_snap is None:
            out.append(None)
            continue
        past = pick_snapshot(store, now - datetime.timedelta(seconds=w),
                             window_tolerance(w))
        out.append(speed(key_fn(now_snap), key_fn(past) if past else None))
    return out


def _sector_key(snap, codes):
    """一个赛道在某条快照上的中位涨跌幅。"""
    return aggregate([snap["r"].get(c) for c in codes])[0]


def build_state(store, now, holdings=HOLD_CODES, live_r=None):
    """组装 render_panel / render_strip 需要的 state。

    store 为空或过期时三个窗口全部返回 None——不用任何别的粒度的数据源凑。

    live_r 是当次实时行情的 {代码: 涨跌幅}，用来给池外持仓票（长鑫科技）
    出「相对强弱」那一节的数字。它不落盘，所以速度栏是 None。
    """
    status = store_status(store, now)
    cur = store[-1]["r"] if store else {}
    rs_now = {c: v for c, v in cur.items() if v is not None}
    br = breadth(rs_now)
    pool_median, valid = aggregate(list(rs_now.values()))
    live_r = live_r or dict(rs_now)

    rows = []
    # 一、每只持仓票各一行
    for code in holdings:
        rows.append({"name": ig.POOL.get(code, HOLD_NAMES.get(code, code)),
                     # 池外票不落盘，速度是结构性缺失，渲染成「—」不是「不可用」
                     "dash": code not in POOL_CODES,
                     "speeds": _speeds_for(store, now,
                                           lambda s, c=code: s["r"].get(c))})
    # 二、持仓票所属赛道各一行，同赛道只出一次，池外票没有赛道直接跳过
    seen_sectors = []
    for code in holdings:
        sec = ig.SECTOR.get(code)
        if sec and sec not in seen_sectors:
            seen_sectors.append(sec)
    for sec in seen_sectors:
        members = [c for c, s in ig.SECTOR.items() if s == sec]
        rows.append({"name": f"{sec}({len(members)}只)",
                     "speeds": _speeds_for(store, now,
                                           lambda s, m=members: _sector_key(s, m))})
    # 三、科技池
    rows.append({"name": f"科技池({valid}只)",
                 "speeds": _speeds_for(
                     store, now,
                     lambda s: aggregate(list(s["r"].values()))[0])})
    # 四、创业板指
    rows.append({"name": IDX_NAMES["sz399006"],
                 "speeds": _speeds_for(store, now,
                                       lambda s: s["idx"].get("sz399006"))})

    hold_rows = []
    all_rs = list(rs_now.values())
    for code in holdings:
        r = live_r.get(code)
        place, total = (rank(r, all_rs) if code in POOL_CODES else (None, len(all_rs)))
        hold_rows.append({"name": ig.POOL.get(code, HOLD_NAMES.get(code, code)),
                          "r": r, "excess": excess(r, pool_median),
                          "rank": (place, total),
                          "in_pool": code in POOL_CODES})

    first = live_r.get(holdings[0]) if holdings else None
    return {"ts": now.strftime(TS_FMT), "valid": valid, "total": len(POOL_CODES),
            "status": status, "rows": rows, "breadth": br,
            "holdings": hold_rows, "verdict": verdict(br, first),
            "pool_median": pool_median, "dropped": 0}


MARKET_TZ = zoneinfo.ZoneInfo("Asia/Shanghai")


def now_bj():
    """当前北京时间，去掉 tzinfo 以便与落盘的时间戳字符串直接比较。

    2026-08-07 实测踩到的坑：本机时区是 JST，比北京快 1 小时。
    用 datetime.datetime.now() 取本机时间去比对 A 股时段，12 个时点里错 8 个——
    误采北京 08:30-09:29 的盘前，漏采 10:31-11:30 和 14:01-15:00 两段，
    尾盘整整一小时全丢，而那正是最需要看盘的时段。

    仓库里 intraday_guide.py:203 和 intraday_collector.py:52 早就定了
    MARKET_TZ 这个约定，这里对齐它。所有取"现在"的地方都必须走这个函数，
    不许直接调 datetime.datetime.now()。
    """
    return datetime.datetime.now(MARKET_TZ).replace(tzinfo=None)


def main():
    """命令行入口：读盘、组装、打印完整面板。"""
    import stock_watch as sw

    now = now_bj()
    rotate_store(now.strftime("%Y-%m-%d"))
    codes = merge_codes(sw.load_config())
    quotes = sw.fetch_quotes(codes)
    pool = parse_pool_quotes(quotes)
    snap = {"t": now.strftime(TS_FMT),
            "r": {c: round(v["r"], 3) for c, v in pool.items() if c in POOL_CODES},
            "idx": {c: round(v["r"], 3) for c, v in pool.items() if c in IDX_CODES}}
    append_store(snap)
    store = load_store(max(WINDOWS) * 2, now=now)
    live_r = {c: v["r"] for c, v in pool.items()}
    print(render_panel(build_state(store, now, live_r=live_r)))


if __name__ == "__main__":
    main()
