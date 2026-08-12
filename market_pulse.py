"""盘中涨跌速度监控 — 回答「我的票在跌，是独跌还是全市场同跌」。

设计文档：docs/market_pulse/spec/2026-08-07-market_pulse-spec.md

只描述已经发生的涨跌，不做方向预测。
"""
import datetime
import json
import os
import sys
import time
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
#
# 隐式耦合（已知取舍，不是 bug）：merge_codes 只合并 POOL_CODES 和 IDX_CODES，
# 不合并 HOLD_CODES。长鑫科技（sh688825）在池外，它的相对强弱数据能拿到，
# 全靠它恰好在用户自选列表 stock_watch.json 里而被一起请求到。
# 一旦把它移出自选，请求里就没有它，相对强弱那一节会静默全变「—」，面板不提示原因。
# 之所以不主动把 HOLD_CODES 合进请求，是为了不让池外票混入宽度和排名的分母——
# 那是刻意的口径选择，改了会影响判定。所以这里保持依赖自选，接受移出自选即失效的代价。
HOLD_CODES = ("sz300308", "sz301526", "sh688825")
HOLD_NAMES = {"sh688825": "长鑫科技"}

WINDOWS = (10, 60, 300)      # 速度窗口，单位秒
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


def sector_mean(values):
    """一个赛道的平均涨跌幅，缺数据的成员不参与。

    赛道只用平均数、不用 aggregate 的中位数：赛道成员少（14 个赛道里 12 个
    只有 ≤3 只），而 3 个数的中位数就是中间那只票自己。2026-08-11 实测，
    中际旭创在光模块三只里全天 3800 条快照 100% 都是中间那只，于是赛道行
    与个股行逐位相同——面板要回答的「独跌还是同跌」在这一行上失效了。

    池子层面仍用 aggregate 的中位数：38 只里有涨停跌停的极端票，那里平均数
    会被拉偏，而成员多不会退化。
    """
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return st.fmean(valid)


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
    3 秒采样下，最短的 10 秒窗口容差 3.3 秒刚好覆盖一个采样间隔——
    再窄就会因为错过一次采样而频繁标「不可用」。
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


def is_trading_day(day):
    """周一到周五算交易日。入参 "2026-08-10"。

    **只排除周末，不含法定节假日**（春节、国庆这些还是会采到重复快照）。
    要做全的话得维护一张交易日历，那是另一件事；周末已经占了非交易日的
    绝大多数，先解决它。
    """
    return parse_ts(day + " 00:00:00").weekday() < 5


def append_store(snap, path=STORE):
    """把一条快照追加落盘，返回是否真的写了。

    非交易时段直接跳过：午休和盘后行情不变，写进去只是一堆重复记录，
    还会污染速度窗口的取值。
    """
    day, hhmm = snap["t"].split(" ")
    if not is_trading_day(day) or not in_session(hhmm):
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
    """只保留 today 当天的记录，清掉其余，返回是否动过文件。

    2026-08-10 踩到的坑：原实现只比对最后一行的日期，而进程跨日运行时
    最后一行恰好是今天，于是更早那几天的数据永远清不掉——文件从预估的
    每天 1.7 MB 涨到 9 MB，里面混着 8/8、8/9 两个周末的重复快照。
    改成逐行过滤，混了几天都能清干净。

    昨天的涨跌幅接到今天的序列上会算出跨夜跳空的假速度，所以必须清。
    """
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as fh:
        lines = [x for x in fh if x.strip()]
    keep = []
    for line in lines:
        try:
            if json.loads(line)["t"].split(" ")[0] == today:
                keep.append(line)
        except (ValueError, KeyError, IndexError):
            continue          # 损坏行一并丢掉
    if len(keep) == len(lines):
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(keep)
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


WINDOW_LABELS = {10: "10秒", 60: "1分钟", 300: "5分钟"}

# 速率曲线：把「最近 5 分钟这一行的速度」画成一列火柴图，补上三列静态
# 数字看不出的东西——同样是 -0.10 pp，一直在掉和刚从 +0.30 掉下来
# 是两回事，而现在的表读不出这个区别。
SPARK_LEVELS = "▁▂▃▄▅▆▇"   # 奇数档：正中那格专门表示速度 0
SPARK_GAP = "·"             # 该点没采到，和「速度为 0」必须分开
SPARK_SPAN = 300            # 画多长的历史，秒（与最右那列窗口对齐）
SPARK_POINTS = 20           # 画多少个点，300/20 = 每 15 秒一点
SPARK_WINDOW = 10           # 每个点算哪个窗口的速度（最灵敏的那一列）


def spark_scale(series_list):
    """全表共用的纵向刻度 = 所有行所有点里绝对值最大的那个。

    共用刻度而不是每行自己缩放：各自缩放会把速度只有 0.01 pp 的安静行
    画得和 0.5 pp 的剧烈行一样高，而这张表存在的意义就是横向比较
    「我的票 vs 赛道 vs 池子」。全 0 或全缺时返回 None，让上层画成缺口，
    不拿 0 去做除数。
    """
    vals = [abs(v) for s in series_list for v in s if v is not None]
    top = max(vals) if vals else 0.0
    return top if top > 0 else None


def sparkline(values, scale):
    """把一串有符号的速度画成火柴图，0 落在正中那格。

    scale 是满格代表的 pp 数（见 spark_scale）。超出刻度贴到端点而不抛错——
    刻度是全表共用的，个别行超出是正常的。
    """
    if not scale:
        return SPARK_GAP * len(values)
    top = len(SPARK_LEVELS) - 1
    mid = top / 2.0
    out = []
    for v in values:
        if v is None:
            out.append(SPARK_GAP)
            continue
        pos = mid + max(-1.0, min(1.0, v / scale)) * mid
        out.append(SPARK_LEVELS[int(round(pos))])
    return "".join(out)


def _speed_series(store, now, key_fn, window=SPARK_WINDOW,
                  points=SPARK_POINTS, step=None):
    """一行在最近 points×step 秒里的速度序列，左旧右新。

    每个点算的是「截至该时刻、跨度 window 秒」的速度，和速度表最左那列同口径。
    取不到快照的点补 None 而不补 0——0 的意思是「没动」。
    """
    if step is None:
        step = SPARK_SPAN // points
    tol = window_tolerance(window)
    out = []
    for i in range(points - 1, -1, -1):
        end = now - datetime.timedelta(seconds=i * step)
        a = pick_snapshot(store, end, tol)
        b = pick_snapshot(store, end - datetime.timedelta(seconds=window), tol)
        out.append(speed(key_fn(a) if a else None, key_fn(b) if b else None))
    return out


def _fmt_pct(v):
    """涨跌幅显示，None 时给「—」而不是崩掉。"""
    return "—" if v is None else f"{v:+.2f}%"


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
    sc = state.get("spark_scale")
    spark_head = (f"最近5分钟（{SPARK_WINDOW}秒速度，"
                  + (f"满格±{sc:.2f}pp）" if sc else "全表未动或无数据）"))
    lines.append("  " + _pad_l("", 16)
                 + "".join(_pad_r(WINDOW_LABELS[w], 9) for w in WINDOWS)
                 + "  " + spark_head)
    for row in state["rows"]:
        dash = row.get("dash", False)
        lines.append("  " + _pad_l(row["name"], 16)
                     + "".join(_pad_r(_fmt_speed(v, dash), 9)
                               for v in row["speeds"])
                     + "  " + row.get("spark", ""))
    if state["status"][0] != "ok":
        lines.append(f"  {state['status'][1]}")
    lines.append("")

    br = state["breadth"]
    lines.append("【宽度】")
    lines.append(f"  池内涨跌幅  中位数 {_fmt_pct(state.get('pool_median'))}"
                 f"   平均 {_fmt_pct(state.get('pool_mean'))}")
    lines.append(f"  池内 上涨 {br['up']} / 平盘 {br['flat']} / 下跌 {br['down']}")
    lines.append(f"  最近 1 分钟翻向：涨转跌 {br['flip_down']} 只，"
                 f"跌转涨 {br['flip_up']} 只")
    lines.append("")

    got, why = state["verdict"]
    lines.append(f"【判定】{why}" if got is None else f"【判定】{got} —— {why}")
    if state.get("dropped"):
        lines.append(f"  跳过 {state['dropped']} 行损坏记录")
    return "\n".join(lines)


def render_strip(state):
    """悬浮窗单行文案，显示宽度不超过 40。

    2026-08-10 用户要求精简：不再显示持仓票的排名和超额，只给池子整体
    涨跌幅、涨跌家数和判定。判定保留，它才是「要不要紧」的直接答案。
    """
    got, _ = state["verdict"]
    br = state["breadth"]
    if got is None:
        return "样本不足" if state["status"][0] == "ok" else "行情不可用"
    full = (f"池{_fmt_pct(state.get('pool_median'))} "
            f"{br['up']}涨{br['down']}跌 {got}")
    return full if display_width(full) <= 40 else f"{br['up']}/{br['down']} {got}"


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
        # 精度在这里一次性定死，下游不许再各自舍入。
        # 2026-08-07 踩到：main() 落盘时 round(r,3)、live_r 用原始值，
        # 于是持仓票自己的舍入值进了对比池，rank 拿原始值比较时把自己
        # 也数了进去，面板出现「排名 39/38（前 103%）」。
        out[q["code"]] = {"r": round((px - pc) / pc * 100, 3), "px": px}
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
    """一个赛道在某条快照上的平均涨跌幅。"""
    return sector_mean([snap["r"].get(c) for c in codes])


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
    _vals = [v for v in rs_now.values() if v is not None]
    pool_mean = st.mean(_vals) if _vals else None
    live_r = live_r or dict(rs_now)

    rows = []
    # 一、每只持仓票各一行
    for code in holdings:
        rows.append({"name": ig.POOL.get(code, HOLD_NAMES.get(code, code)),
                     # 池外票不落盘，速度是结构性缺失，渲染成「—」不是「不可用」
                     "dash": code not in POOL_CODES,
                     "key": (lambda s, c=code: s["r"].get(c))})
    # 二、持仓票所属赛道各一行，同赛道只出一次，池外票没有赛道直接跳过
    seen_sectors = []
    for code in holdings:
        sec = ig.SECTOR.get(code)
        if sec and sec not in seen_sectors:
            seen_sectors.append(sec)
    for sec in seen_sectors:
        members = [c for c, s in ig.SECTOR.items() if s == sec]
        rows.append({"name": f"{sec}({len(members)}只)",
                     "key": (lambda s, m=members: _sector_key(s, m))})
    # 三、科技池
    rows.append({"name": f"科技池({valid}只)",
                 "key": (lambda s: aggregate(list(s["r"].values()))[0])})
    # 四、创业板指
    rows.append({"name": IDX_NAMES["sz399006"],
                 "key": (lambda s: s["idx"].get("sz399006"))})

    # 每行的三个窗口速度与最近 5 分钟的速度序列，都从同一个 key 取值，
    # 保证曲线和最左那列是同一个口径。
    for row in rows:
        row["speeds"] = _speeds_for(store, now, row["key"])
        row["series"] = (None if row.get("dash")
                         else _speed_series(store, now, row["key"]))
    # 刻度全表共用，所以必须等所有行的序列都算完才能定（见 spark_scale）
    scale = spark_scale([r["series"] for r in rows if r["series"]])
    for row in rows:
        # 池外票不落盘，整条曲线是结构性缺失，和「采到了但没动」不是一回事
        row["spark"] = ("—" if row["series"] is None
                        else sparkline(row["series"], scale))
        del row["key"]

    hold_rows = []
    all_rs = list(rs_now.values())
    for code in holdings:
        # 池内票的 r 一律取 store 里的那个值，不取 live_r。
        # 2026-08-07 的「排名 39/38」就是这两个来源精度分叉造成的：
        # 这只票自己的 store 值比它的 live_r 值大，rank 把自己也数了进去。
        # 只靠"两处都记得 round 到同样位数"是约定，会随新调用点复发；
        # 让池内票的比较值和被比较的池子同源，才是结构上消灭这一类 bug。
        # live_r 只服务池外持仓票（长鑫科技），它们本来就不在 all_rs 里。
        r = rs_now.get(code) if code in POOL_CODES else live_r.get(code)
        place, total = (rank(r, all_rs) if code in POOL_CODES else (None, len(all_rs)))
        hold_rows.append({"name": ig.POOL.get(code, HOLD_NAMES.get(code, code)),
                          "r": r, "excess": excess(r, pool_median),
                          "rank": (place, total),
                          "in_pool": code in POOL_CODES})

    first = (rs_now.get(holdings[0]) if holdings and holdings[0] in POOL_CODES
             else (live_r.get(holdings[0]) if holdings else None))
    return {"ts": now.strftime(TS_FMT), "valid": valid, "total": len(POOL_CODES),
            "status": status, "rows": rows, "breadth": br,
            "holdings": hold_rows, "verdict": verdict(br, first),
            "pool_median": pool_median, "pool_mean": pool_mean, "dropped": 0,
            "spark_scale": scale}


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


WATCH_DEFAULT = 15      # --watch 不带参数时的刷新间隔，秒


def parse_args(argv):
    """解析命令行参数，返回刷新间隔（秒）或 None（单次运行）。

    --watch        每 15 秒刷新
    --watch 5      每 5 秒刷新

    参数非法直接抛 ValueError，不静默退回默认值——静默会让人以为
    自己设的间隔生效了，而实际上没有。
    """
    if not argv:
        return None
    if argv[0] != "--watch":
        raise ValueError(f"未知参数: {argv[0]}；只支持 --watch [秒]")
    if len(argv) == 1:
        return WATCH_DEFAULT
    if len(argv) > 2:
        raise ValueError(f"参数过多: {argv[2:]}")
    try:
        n = int(argv[1])
    except ValueError:
        raise ValueError(f"刷新间隔要是正整数，收到 {argv[1]!r}") from None
    if n < 1:
        raise ValueError(f"刷新间隔要是正整数，收到 {n}")
    return n


def _snapshot_once():
    """取一次数、落盘、返回渲染好的面板文本。"""
    import stock_watch as sw

    now = now_bj()
    rotate_store(now.strftime("%Y-%m-%d"))
    quotes = sw.fetch_quotes(merge_codes(sw.load_config()))
    pool = parse_pool_quotes(quotes)
    snap = {"t": now.strftime(TS_FMT),
            "r": {c: v["r"] for c, v in pool.items() if c in POOL_CODES},
            "idx": {c: v["r"] for c, v in pool.items() if c in IDX_CODES}}
    append_store(snap)
    store = load_store(max(WINDOWS) * 2, now=now)
    live_r = {c: v["r"] for c, v in pool.items()}
    return render_panel(build_state(store, now, live_r=live_r))


def main(argv=None):
    """命令行入口。默认单次运行；--watch 常驻并定时重绘。"""
    interval = parse_args(list(sys.argv[1:] if argv is None else argv))
    if interval is None:
        print(_snapshot_once())
        return
    print(f"每 {interval} 秒刷新一次，Ctrl+C 退出\n")
    try:
        while True:
            panel = _snapshot_once()
            # 先算好再清屏，取数失败时不会留下一个空屏幕
            sys.stdout.write("\033[H\033[J")
            print(panel)
            print(f"\n（每 {interval} 秒刷新，Ctrl+C 退出）")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
