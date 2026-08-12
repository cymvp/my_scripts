"""market_pulse 纯函数的单元测试（不联网、不读盘）。"""
import datetime

import pytest

import market_pulse as mp


# --- 常量 -----------------------------------------------------------------

def test_pool_codes_has_38():
    """池子固定 38 只，与 intraday_guide.POOL 同源同序。"""
    import intraday_guide as ig
    assert len(mp.POOL_CODES) == 38
    assert list(mp.POOL_CODES) == list(ig.POOL)


def test_windows_are_10_60_300():
    assert mp.WINDOWS == (10, 60, 300)


# --- merge_codes 合并去重 --------------------------------------------------

def test_merge_codes_dedups_overlap():
    """自选里已有的池子票不能重复出现。

    实际场景：自选里有中际旭创和国际复材，它们同时也在 38 只池子里。
    """
    watch = ["sz300308", "hk03308", "sh601288"]
    out = mp.merge_codes(watch, pool=("sz300308", "sz300502"), idx=("sz399006",))
    assert out == ["sz300308", "hk03308", "sh601288", "sz300502", "sz399006"]


def test_merge_codes_keeps_watch_order_first():
    """自选顺序必须原样保留在最前，界面按这个顺序渲染。"""
    watch = ["sh601288", "sz300308"]
    out = mp.merge_codes(watch, pool=("sz300308",), idx=())
    assert out[:2] == ["sh601288", "sz300308"]


def test_merge_codes_empty_watch():
    out = mp.merge_codes([], pool=("sz300308", "sz300502"), idx=("sz399006",))
    assert out == ["sz300308", "sz300502", "sz399006"]


def test_merge_codes_real_sizes():
    """真实规模：自选 10 个 A 股 + 池子 38 + 指数 3，重叠 6 个，去重后 45。"""
    watch = ["sz300308", "sz301526", "sh688825", "sh601288", "sh588170",
             "sz002463", "sh688256", "sh603986", "sh688008", "sh513120"]
    out = mp.merge_codes(watch)
    assert len(out) == 45
    assert len(set(out)) == 45


# --- in_session 交易时段 ---------------------------------------------------

@pytest.mark.parametrize("hhmm,expected", [
    ("09:14", False),   # 竞价前
    ("09:20", False),   # 集合竞价——虚拟撮合价，混进速度序列会造假信号
    ("09:29", False),
    ("09:30", True),    # 开盘首笔
    ("10:30", True),
    ("11:29", True),
    ("11:30", True),    # 上午收盘那一笔算
    ("11:31", False),   # 午休
    ("12:00", False),
    ("12:59", False),
    ("13:00", True),    # 下午开盘
    ("14:57", True),
    ("15:00", True),    # 收盘那一笔算
    ("15:01", False),   # 盘后
    ("23:00", False),
])
def test_in_session(hhmm, expected):
    assert mp.in_session(hhmm) is expected


def test_in_session_accepts_seconds():
    """带秒的时间戳也要认，落盘里存的是 HH:MM:SS。"""
    assert mp.in_session("10:30:15") is True
    assert mp.in_session("12:00:01") is False


def test_in_session_rejects_garbage():
    """格式不对直接抛，不静默返回 False——那会让采集悄悄停掉。"""
    with pytest.raises(ValueError):
        mp.in_session("abc")


# --- speed 速度 ------------------------------------------------------------

def test_speed_normal():
    """13:24:00 价 979.00（r=+2.51%），13:24:15 价 977.00（r=+2.30%）。

    v = 2.30 − 2.51 = −0.21 百分点，读作「最近 15 秒涨跌幅掉了 0.21 个百分点」。
    """
    assert mp.speed(2.30, 2.51) == pytest.approx(-0.21)


def test_speed_zero_when_unchanged():
    assert mp.speed(2.30, 2.30) == 0.0


def test_speed_returns_none_when_past_missing():
    """缺历史返回 None，不返回 0——0 会被读成「没动」，那是完全不同的意思。"""
    assert mp.speed(2.30, None) is None


def test_speed_returns_none_when_now_missing():
    assert mp.speed(None, 2.51) is None


# --- aggregate 中位数聚合 --------------------------------------------------

def test_aggregate_odd_count():
    assert mp.aggregate([-0.5, -0.1, 2.0]) == (pytest.approx(-0.1), 3)


def test_aggregate_even_count():
    """偶数个取中间两个的均值。"""
    val, n = mp.aggregate([-0.4, -0.2, 0.1, 0.3])
    assert val == pytest.approx(-0.05)
    assert n == 4


def test_aggregate_drops_none():
    val, n = mp.aggregate([-0.5, None, -0.1])
    assert val == pytest.approx(-0.3)
    assert n == 2


def test_aggregate_all_none():
    assert mp.aggregate([None, None]) == (None, 0)


def test_aggregate_empty():
    assert mp.aggregate([]) == (None, 0)


def test_aggregate_uses_median_not_mean():
    """必须是中位数。

    2026-08-07 实测：PCB/覆铜板赛道中位 +7.62%、MLCC 只有 +0.39%，
    平均数会被少数暴涨票拉偏。
    """
    val, _ = mp.aggregate([0.1, 0.1, 0.1, 20.0])
    assert val == pytest.approx(0.1)      # 中位数 0.1；平均数会是 5.075


# --- sector_mean 赛道均值 --------------------------------------------------

def test_sector_mean_basic():
    assert mp.sector_mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)


def test_sector_mean_drops_none():
    """缺数据的成员不参与平均，不当 0 计。"""
    assert mp.sector_mean([1.0, None, 3.0]) == pytest.approx(2.0)


def test_sector_mean_all_none():
    assert mp.sector_mean([None, None]) is None


def test_sector_mean_empty():
    assert mp.sector_mean([]) is None


def test_sector_mean_does_not_collapse_to_middle_member():
    """三只成员时不能退化成中间那只自己。

    2026-08-11 13:41:12 实测：光模块三只 新易盛 +4.542 / 中际旭创 +2.942 /
    天孚通信 +0.883，中位数正好等于中际旭创，赛道行与个股行逐位相同、
    这个对比失去意义。当天 3800 条快照里这个重合率是 100%。
    """
    vals = [4.542, 2.942, 0.883]
    assert mp.sector_mean(vals) == pytest.approx(2.789)
    assert mp.sector_mean(vals) != pytest.approx(2.942)


def test_sector_key_uses_mean(monkeypatch):
    """_sector_key 走均值：赛道速度不再等于中间那只成员的速度。"""
    snap_a = {"r": {"a": 4.542, "b": 2.942, "c": 0.883}}
    snap_b = {"r": {"a": 4.642, "b": 2.942, "c": 0.883}}
    codes = ["a", "b", "c"]
    sec_speed = mp.speed(mp._sector_key(snap_b, codes),
                         mp._sector_key(snap_a, codes))
    stock_speed = mp.speed(snap_b["r"]["b"], snap_a["r"]["b"])
    assert stock_speed == pytest.approx(0.0)       # 中际旭创没动
    assert sec_speed == pytest.approx(0.1 / 3)     # 但赛道动了


# --- sparkline 速率曲线 ----------------------------------------------------

def test_spark_levels_are_odd_so_zero_has_its_own_glyph():
    """必须是奇数档，中间那格专门表示 0。

    速度是有符号的（可正可负），偶数档时 0 会落在两格之间，
    「没动」和「微涨」会画成同一个字符。
    """
    assert len(mp.SPARK_LEVELS) % 2 == 1


def test_sparkline_zero_series_is_all_center():
    """全 0 的速度序列画成一条中线，不是空的也不是贴底。"""
    center = mp.SPARK_LEVELS[len(mp.SPARK_LEVELS) // 2]
    assert mp.sparkline([0.0, 0.0, 0.0], scale=1.0) == center * 3


def test_sparkline_max_and_min_hit_the_ends():
    assert mp.sparkline([1.0], scale=1.0) == mp.SPARK_LEVELS[-1]
    assert mp.sparkline([-1.0], scale=1.0) == mp.SPARK_LEVELS[0]


def test_sparkline_clamps_beyond_scale():
    """超出刻度不抛错、贴到端点——刻度是全表共用的，个别行会超。"""
    assert mp.sparkline([5.0], scale=1.0) == mp.SPARK_LEVELS[-1]
    assert mp.sparkline([-5.0], scale=1.0) == mp.SPARK_LEVELS[0]


def test_sparkline_missing_point_is_a_visible_gap():
    """缺采样画成「·」，不画成中线——中线的意思是「没动」，两者不能混。"""
    out = mp.sparkline([1.0, None, -1.0], scale=1.0)
    assert out == mp.SPARK_LEVELS[-1] + mp.SPARK_GAP + mp.SPARK_LEVELS[0]


def test_sparkline_all_missing():
    assert mp.sparkline([None, None], scale=1.0) == mp.SPARK_GAP * 2


def test_sparkline_scale_zero_or_none_gives_gaps():
    """全表都没动时刻度是 0，不能拿它做除数。"""
    assert mp.sparkline([0.0, 0.0], scale=0.0) == mp.SPARK_GAP * 2
    assert mp.sparkline([0.0], scale=None) == mp.SPARK_GAP


def test_spark_scale_is_shared_max_abs():
    """刻度 = 所有行所有点里绝对值最大的那个。

    共用刻度而不是每行自己缩放：各自缩放会把速度 0.01pp 的安静行
    画得和 0.5pp 的剧烈行一样，而这张表存在的意义就是横向比较。
    """
    assert mp.spark_scale([[0.1, -0.2], [0.5, None]]) == pytest.approx(0.5)


def test_spark_scale_all_none():
    assert mp.spark_scale([[None], [None, None]]) is None


def test_spark_scale_all_zero():
    """全 0 时返回 None，让上层画成缺口而不是除零。"""
    assert mp.spark_scale([[0.0, 0.0]]) is None


def test_spark_scale_empty():
    assert mp.spark_scale([]) is None


def test_speed_series_walks_back_in_time():
    """序列按时间从左到右，最右是最新。

    构造 13:24:00 起每 10 秒一条、涨跌幅每条 +0.10：10 秒窗口的速度
    恒为 +0.10，序列 3 点全是 +0.10。
    """
    snaps = [{"t": f"2026-08-11 13:24:{s:02d}",
              "r": {"a": 1.0 + i * 0.10}, "idx": {}}
             for i, s in enumerate(range(0, 60, 10))]
    out = mp._speed_series(snaps, mp.parse_ts("2026-08-11 13:24:50"),
                           lambda s: s["r"].get("a"),
                           window=10, points=3, step=10)
    assert out == [pytest.approx(0.10)] * 3


def test_speed_series_gives_none_where_no_snapshot():
    """历史不够长的左侧补 None，不补 0。"""
    snaps = [{"t": "2026-08-11 13:24:50", "r": {"a": 1.0}, "idx": {}},
             {"t": "2026-08-11 13:25:00", "r": {"a": 1.2}, "idx": {}}]
    out = mp._speed_series(snaps, mp.parse_ts("2026-08-11 13:25:00"),
                           lambda s: s["r"].get("a"),
                           window=10, points=3, step=10)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(0.2)


def test_render_panel_has_spark_column():
    st = _state()
    out = mp.render_panel(st)
    assert "最近5分钟" in out


def test_render_panel_spark_header_states_full_height():
    """满格代表多少 pp 必须打出来，否则曲线高度没有含义。"""
    st = _state()
    st["spark_scale"] = 0.25
    out = mp.render_panel(st)
    assert "满格±0.25pp" in out


def test_render_panel_spark_scale_none_says_so():
    st = _state()
    st["spark_scale"] = None
    out = mp.render_panel(st)
    assert "最近5分钟" in out


def test_render_panel_pool_outsider_spark_is_dash():
    """池外票不落盘，曲线是结构性缺失，画「—」不是缺口点。"""
    st = _state()
    for row in st["rows"]:
        if row.get("dash"):
            row["spark"] = "—"
    out = mp.render_panel(st)
    assert "—" in out


# --- breadth 宽度 ----------------------------------------------------------

def test_breadth_counts():
    """2026-08-07 11:30 实测：38 只里上涨 34、下跌 4、平盘 0。"""
    rs = {f"c{i}": 1.0 for i in range(34)}
    rs.update({f"d{i}": -1.0 for i in range(4)})
    br = mp.breadth(rs)
    assert (br["up"], br["down"], br["flat"], br["valid"]) == (34, 4, 0, 38)


def test_breadth_counts_flat():
    br = mp.breadth({"a": 1.0, "b": 0.0, "c": -1.0})
    assert (br["up"], br["down"], br["flat"]) == (1, 1, 1)


def test_breadth_skips_none():
    """停牌票的 r 是 None，从有效数里剔除。"""
    br = mp.breadth({"a": 1.0, "b": None, "c": -1.0})
    assert br["valid"] == 2


def test_breadth_flip_down():
    """涨转跌算一次翻向。"""
    br = mp.breadth({"a": -0.1}, {"a": 0.3})
    assert br["flip_down"] == 1
    assert br["flip_up"] == 0


def test_breadth_flip_up():
    br = mp.breadth({"a": 0.2}, {"a": -0.5})
    assert br["flip_up"] == 1
    assert br["flip_down"] == 0


def test_breadth_flat_is_not_a_flip():
    """从 +0.2 变成 0.0 不算翻向——平盘不是方向。"""
    br = mp.breadth({"a": 0.0}, {"a": 0.2})
    assert br["flip_down"] == 0
    assert br["flip_up"] == 0


def test_breadth_no_past_gives_zero_flips():
    br = mp.breadth({"a": 1.0})
    assert br["flip_down"] == 0 and br["flip_up"] == 0


# --- verdict 判定 ----------------------------------------------------------

def test_verdict_alone_falling():
    """池内 34/38 上涨（89% ≥ 60%），你的票在跌 —— 独跌。"""
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    assert mp.verdict(br, -1.2)[0] == "独跌"


def test_verdict_falling_together():
    br = {"up": 5, "down": 30, "flat": 3, "valid": 38}
    assert mp.verdict(br, -1.2)[0] == "同跌"


def test_verdict_rising_together():
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    assert mp.verdict(br, 2.35)[0] == "同涨"


def test_verdict_mixed():
    br = {"up": 20, "down": 18, "flat": 0, "valid": 38}
    assert mp.verdict(br, -1.2)[0] == "分化"


def test_verdict_boundary_is_inclusive():
    """正好 60% 算命中。38 只的 60% 是 22.8，所以 23 只命中。"""
    br = {"up": 23, "down": 15, "flat": 0, "valid": 38}
    assert mp.verdict(br, -0.5)[0] == "独跌"
    br2 = {"up": 22, "down": 16, "flat": 0, "valid": 38}
    assert mp.verdict(br2, -0.5)[0] == "分化"


def test_verdict_denominator_follows_valid_count():
    """分母是有效票数，不是固定的 38。

    35 只有效时 60% 对应 21 只，所以 21 只上涨就命中。
    """
    br = {"up": 21, "down": 14, "flat": 0, "valid": 35}
    assert mp.verdict(br, -0.5)[0] == "独跌"


def test_verdict_flat_stock_is_mixed():
    """个股正好平盘一律判分化，不进前三行。"""
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    assert mp.verdict(br, 0.0)[0] == "分化"


def test_verdict_returns_none_when_sample_too_small():
    """有效票不足 20 只不出判定。

    只有 18 只返回而其中 12 只在涨，算出 67% 照样会打印「同涨」，
    但另外 20 只的涨跌完全未知；若丢的恰好是 12 只科创板票，
    剩下的池子就缺了半导体设备和晶圆制造，结论会系统性偏向剩余赛道。
    这是方向性错误，不是精度问题。
    """
    br = {"up": 12, "down": 6, "flat": 0, "valid": 18}
    got, why = mp.verdict(br, -1.2)
    assert got is None
    assert "样本不足" in why
    assert "18" in why


def test_verdict_note_mentions_ratio():
    br = {"up": 34, "down": 4, "flat": 0, "valid": 38}
    _, why = mp.verdict(br, 2.35)
    assert "34/38" in why


# --- excess 超额 -----------------------------------------------------------

def test_excess_normal():
    """2026-08-07 11:30 实测：中际旭创 +2.35%，科技池中位 +1.66%。"""
    assert mp.excess(2.35, 1.66) == pytest.approx(0.69)


def test_excess_negative():
    assert mp.excess(-1.20, 1.66) == pytest.approx(-2.86)


def test_excess_none_when_bench_missing():
    assert mp.excess(2.35, None) is None


def test_excess_none_when_stock_missing():
    assert mp.excess(None, 1.66) is None


# --- rank 池内排名 ---------------------------------------------------------

def test_rank_normal():
    """名次 = 严格大于你的票数 + 1。"""
    assert mp.rank(3.0, [5.0, 3.0, 1.0]) == (2, 3)


def test_rank_first():
    assert mp.rank(5.0, [5.0, 3.0, 1.0]) == (1, 3)


def test_rank_ties_share_place_and_skip():
    """并列给相同名次，之后跳号：[5.0, 3.0, 3.0, 1.0] 的名次是 1、2、2、4。"""
    all_rs = [5.0, 3.0, 3.0, 1.0]
    assert mp.rank(5.0, all_rs) == (1, 4)
    assert mp.rank(3.0, all_rs) == (2, 4)
    assert mp.rank(1.0, all_rs) == (4, 4)


def test_rank_denominator_is_valid_count():
    """分母是有效票数：38 只里 3 只停牌，分母是 35。"""
    all_rs = [1.0] * 35 + [None, None, None]
    _, n = mp.rank(1.0, all_rs)
    assert n == 35


def test_rank_none_when_stock_missing():
    assert mp.rank(None, [1.0, 2.0]) == (None, 2)


def test_rank_none_when_pool_empty():
    assert mp.rank(1.0, []) == (None, 0)


# --- parse_ts / pick_snapshot ---------------------------------------------

def test_parse_ts():
    got = mp.parse_ts("2026-08-07 13:24:15")
    assert (got.year, got.month, got.day) == (2026, 8, 7)
    assert (got.hour, got.minute, got.second) == (13, 24, 15)


def test_parse_ts_rejects_garbage():
    with pytest.raises(ValueError):
        mp.parse_ts("13:24:15")


def _snap(ts, r=2.35):
    """落盘存的是涨跌幅 r（%），不是价格——见 spec §4.2。"""
    return {"t": ts, "r": {"sz300308": r}, "idx": {}}


def test_pick_snapshot_exact():
    store = [_snap("2026-08-07 13:24:00"), _snap("2026-08-07 13:24:15")]
    got = mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5)
    assert got["t"] == "2026-08-07 13:24:00"


def test_pick_snapshot_within_tolerance():
    """目标 13:24:00、容差 5 秒，13:24:02 的快照可用。"""
    store = [_snap("2026-08-07 13:24:02")]
    got = mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5)
    assert got["t"] == "2026-08-07 13:24:02"


def test_pick_snapshot_beyond_tolerance_returns_none():
    """目标 13:24:00，最近的是 13:23:50，偏差 10 秒 > 容差 5 秒 —— 不可用。

    宁可标注不可用，也不拿一个口径不同的数去凑。
    """
    store = [_snap("2026-08-07 13:23:50")]
    assert mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5) is None


def test_pick_snapshot_picks_nearest():
    store = [_snap("2026-08-07 13:23:58"), _snap("2026-08-07 13:24:04")]
    got = mp.pick_snapshot(store, mp.parse_ts("2026-08-07 13:24:00"), 5)
    assert got["t"] == "2026-08-07 13:23:58"      # 偏差 2 秒 < 4 秒


def test_pick_snapshot_empty_store():
    assert mp.pick_snapshot([], mp.parse_ts("2026-08-07 13:24:00"), 5) is None


def test_pick_snapshot_tolerance_is_third_of_window():
    """15 秒窗口的容差是 5 秒，300 秒窗口是 100 秒。"""
    assert mp.window_tolerance(10) == pytest.approx(10 / 3.0)
    assert mp.window_tolerance(60) == pytest.approx(20.0)
    assert mp.window_tolerance(300) == pytest.approx(100.0)


# --- 落盘 IO --------------------------------------------------------------

def test_append_store_writes_in_session(tmp_path):
    p = tmp_path / "s.jsonl"
    snap = {"t": "2026-08-07 13:24:15", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=str(p)) is True
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_append_store_skips_outside_session(tmp_path):
    """午休不写——避免文件里灌一堆重复快照。"""
    p = tmp_path / "s.jsonl"
    snap = {"t": "2026-08-07 12:00:00", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(snap, path=str(p)) is False
    assert not p.exists()


def test_load_store_roundtrip(tmp_path):
    p = tmp_path / "s.jsonl"
    now = mp.parse_ts("2026-08-07 13:25:00")
    for i in range(10):
        mp.append_store({"t": f"2026-08-07 13:24:{i:02d}",
                         "r": {"sz300308": 2.0 + i * 0.1}, "idx": {}}, path=str(p))
    got = mp.load_store(120, now=now, path=str(p))
    assert len(got) == 10
    assert got[0]["t"] == "2026-08-07 13:24:00"
    assert got[-1]["r"]["sz300308"] == pytest.approx(2.9)


def test_load_store_filters_by_age(tmp_path):
    """只要最近 N 秒的，更早的不读。"""
    p = tmp_path / "s.jsonl"
    for ts in ("13:20:00", "13:24:00", "13:24:50"):
        mp.append_store({"t": f"2026-08-07 {ts}", "r": {}, "idx": {}}, path=str(p))
    got = mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"), path=str(p))
    assert [s["t"] for s in got] == ["2026-08-07 13:24:00", "2026-08-07 13:24:50"]


def test_load_store_skips_corrupt_lines(tmp_path):
    """损坏行跳过继续读，不让整个文件报废。"""
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-07 13:24:00","r":{},"idx":{}}\n'
                 'NOT JSON\n'
                 '{"t":"2026-08-07 13:24:30","r":{},"idx":{}}\n', encoding="utf-8")
    got = mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"), path=str(p))
    assert len(got) == 2


def test_load_store_missing_file(tmp_path):
    assert mp.load_store(120, now=mp.parse_ts("2026-08-07 13:25:00"),
                         path=str(tmp_path / "nope.jsonl")) == []


def test_rotate_store_clears_stale_day(tmp_path):
    """昨天的记录必须清掉——昨收接今开会算出跨夜跳空的假速度。"""
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-06 14:00:00","r":{},"idx":{}}\n', encoding="utf-8")
    assert mp.rotate_store("2026-08-07", path=str(p)) is True
    assert p.read_text(encoding="utf-8") == ""


def test_rotate_store_keeps_today(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"t":"2026-08-07 09:31:00","r":{},"idx":{}}\n', encoding="utf-8")
    assert mp.rotate_store("2026-08-07", path=str(p)) is False
    assert p.read_text(encoding="utf-8") != ""


def test_rotate_store_missing_file(tmp_path):
    assert mp.rotate_store("2026-08-07", path=str(tmp_path / "nope.jsonl")) is False


# --- store_status 三种不可用状态 -------------------------------------------

def test_store_status_not_running_when_empty():
    got, why = mp.store_status([], mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "not_running"
    assert "悬浮窗未启动" in why


def test_store_status_not_running_when_stale():
    """最后一条超过 STALE_SEC（60 秒）视为采集已停。"""
    store = [{"t": "2026-08-07 13:23:00", "r": {}, "idx": {}}]
    got, _ = mp.store_status(store, mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "not_running"


def test_store_status_warming_up():
    """采集刚启动，最早一条距现在不足最长窗口（300 秒）。"""
    store = [{"t": "2026-08-07 13:24:13", "r": {}, "idx": {}},
             {"t": "2026-08-07 13:25:00", "r": {}, "idx": {}}]
    got, why = mp.store_status(store, mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "warming_up"
    assert "47" in why and "300" in why


def test_store_status_ok():
    store = [{"t": "2026-08-07 13:19:00", "r": {}, "idx": {}},
             {"t": "2026-08-07 13:25:00", "r": {}, "idx": {}}]
    got, _ = mp.store_status(store, mp.parse_ts("2026-08-07 13:25:00"))
    assert got == "ok"


# --- 渲染 -----------------------------------------------------------------

def test_display_width_mixes_cjk_and_ascii():
    assert mp.display_width("abc") == 3
    assert mp.display_width("中际旭创") == 8
    assert mp.display_width("池+1.66%") == 8      # 1 个中文(2) + 6 个 ASCII(6)


@pytest.mark.parametrize("s,width", [
    ("中际旭创", 16), ("MLCC(2只)", 16), ("AI算力芯片(3只)", 16), ("", 16),
])
def test_pad_l_aligns_by_display_width(s, width):
    """按显示宽度补齐，不是按码点。

    真实赛道名的中文字数差很多——MLCC(2只) 只有 1 个中文、
    AI算力芯片(3只) 有 5 个。按码点补齐会让面板右缘参差 17 到 22 不等，
    而这个面板的全部价值就是一眼看懂。
    """
    assert mp.display_width(mp._pad_l(s, width)) == width


def test_pad_r_aligns_by_display_width():
    assert mp.display_width(mp._pad_r("10秒", 9)) == 9
    assert mp.display_width(mp._pad_r("-0.21", 9)) == 9


def test_pad_does_not_truncate_when_too_long():
    """超长不截断——截断会把股票名切掉，宁可这一行歪掉。"""
    assert mp._pad_l("中际旭创国际复材长鑫科技", 8) == "中际旭创国际复材长鑫科技"


def _state(**over):
    """构造一个数据齐全的 state，测试按需覆盖字段。"""
    base = {
        "ts": "2026-08-07 13:24:15",
        "valid": 38, "total": 38,
        "status": ("ok", ""),
        "rows": [
            {"name": "中际旭创", "speeds": [-0.21, -0.55, -1.32]},
            {"name": "国际复材", "speeds": [0.30, 0.81, 2.10]},
            {"name": "长鑫科技", "speeds": [None, None, None], "dash": True},
            {"name": "光模块(3只)", "speeds": [-0.15, -0.41, -0.98]},
            {"name": "电子布(3只)", "speeds": [0.22, 0.65, 1.80]},
            {"name": "科技池(38只)", "speeds": [-0.08, -0.22, -0.51]},
            {"name": "创业板指", "speeds": [-0.04, -0.13, -0.30]},
        ],
        "breadth": {"up": 34, "down": 4, "flat": 0, "valid": 38,
                    "flip_down": 3, "flip_up": 0},
        "holdings": [
            {"name": "中际旭创", "r": 2.35, "excess": 0.69, "rank": (17, 38),
             "in_pool": True},
            {"name": "国际复材", "r": 7.24, "excess": 5.58, "rank": (4, 38),
             "in_pool": True},
            {"name": "长鑫科技", "r": -0.17, "excess": -1.83, "rank": (None, 38),
             "in_pool": False},
        ],
        "verdict": ("同涨", "池内 34/38 上涨（89%）、4/38 下跌（11%）"),
        "pool_median": 1.66,
        "pool_mean": 1.20,
        "dropped": 0,
    }
    base.update(over)
    return base


def test_render_panel_has_expected_blocks():
    """面板只留三块：速度、宽度、判定。

    2026-08-10 用户要求精简：不再显示持仓票的排名和超额，
    【相对强弱】整节移除。判定保留——它才是这个模块的核心产出，
    内部仍需要知道持仓票是涨是跌，只是不把排名印出来。
    """
    out = mp.render_panel(_state())
    assert "【速度】" in out and "【宽度】" in out and "【判定】" in out
    assert "【相对强弱】" not in out
    assert "排名" not in out and "超额" not in out
    assert "【判定】" in out


def test_render_panel_shows_speed_numbers():
    out = mp.render_panel(_state())
    assert "-0.21" in out and "-1.32" in out


def test_render_panel_speed_cells_have_no_numbers_when_unavailable():
    """硬约束的守护测试：速度不可用时，那一行不能出现任何数字。

    断言的是速度行本身，不是整个面板。2026-08-07 审查发现的教训：
    原来那版把 status 设成 not_running，于是面板底部会多一行
    「不可用（悬浮窗未启动）」，`"不可用" in out` 由那行满足，
    和速度单元格毫无关系——`_fmt_speed(None)` 就算回归成返回 "+0.00"
    去凑数，那版测试照样通过。所以这里把 status 设成 ok、holdings 清空，
    让「不可用」只可能来自速度单元格，再断言整行没有数字。
    """
    st = _state(rows=[{"name": "中际旭创", "speeds": [None, None, None]}],
                holdings=[], status=("ok", ""))
    line = [x for x in mp.render_panel(st).splitlines() if "中际旭创" in x][0]
    assert "不可用" in line
    assert not any(ch.isdigit() for ch in line)


def test_render_panel_distinguishes_dash_from_unavailable():
    """池外票显示「—」，采集故障显示「不可用」，两者不能混。

    长鑫科技不在 38 只池内、不落盘，它没有速度是结构性的；
    而采集没跑起来是故障。混成一种会让面板自相矛盾。
    """
    st = _state(rows=[{"name": "长鑫科技", "speeds": [None] * 3, "dash": True},
                      {"name": "中际旭创", "speeds": [None] * 3}],
                holdings=[], status=("ok", ""))
    out = mp.render_panel(st).splitlines()
    cx = [x for x in out if "长鑫科技" in x][0]
    zj = [x for x in out if "中际旭创" in x][0]
    assert "—" in cx and "不可用" not in cx
    assert "不可用" in zj and "—" not in zj


def test_render_panel_window_labels_are_human_readable():
    """表头用「1分钟 / 5分钟」，不用「60秒 / 300秒」。

    这个面板的价值是盘中一眼看懂，让读者把 300 秒心算成 5 分钟
    直接削弱它存在的理由。spec §6.1 的样例就是这么写的。
    """
    out = mp.render_panel(_state())
    assert "10秒" in out and "1分钟" in out and "5分钟" in out
    assert "60秒" not in out and "300秒" not in out


def test_render_panel_shows_sample_too_small():
    st = _state(valid=18, verdict=(None, "样本不足（仅 18 只有效，需 20 只）"))
    out = mp.render_panel(st)
    assert "样本不足" in out


def test_render_panel_reports_dropped_lines():
    out = mp.render_panel(_state(dropped=3))
    assert "跳过 3 行损坏记录" in out


def test_render_panel_shows_dash_for_pool_outsider():
    """长鑫科技不在 38 只池内：速度栏是「—」，排名标「不在池内」。

    2026-08-07 审查发现的教训：原来那版只断言 `"不在池内" in out`，
    而那句话来自【相对强弱】节的排名文本，和速度栏渲染毫无关系——
    速度栏渲染成「不可用」（违反 spec）它照样通过。所以这里分别
    定位到两行各自断言。
    """
    rows = [{"name": "长鑫科技", "speeds": [None] * 3, "dash": True}]
    out = mp.render_panel(_state(rows=rows)).splitlines()
    hits = [x for x in out if "长鑫科技" in x]
    assert len(hits) == 1, "相对强弱节已移除，长鑫只应出现在速度栏一次"
    assert "—" in hits[0] and "不可用" not in hits[0]


def test_render_strip_handles_missing_excess():
    """超额为 None 时不能崩，也不能格式化 None。"""
    st = _state(holdings=[{"name": "中际旭创", "r": 2.35, "excess": None,
                           "rank": (17, 38), "in_pool": True}])
    assert mp.display_width(mp.render_strip(st)) <= 40


def test_render_strip_handles_missing_rank():
    st = _state(holdings=[{"name": "长鑫科技", "r": -0.17, "excess": -1.83,
                           "rank": (None, 38), "in_pool": False}])
    assert mp.display_width(mp.render_strip(st)) <= 40


def test_render_strip_handles_empty_holdings():
    out = mp.render_strip(_state(holdings=[]))
    assert "同涨" in out
    assert mp.display_width(out) <= 40


def test_render_panel_has_sector_and_index_rows():
    """面板速度栏固定顺序：持仓票 → 赛道 → 科技池 → 创业板指。"""
    out = mp.render_panel(_state())
    for name in ("光模块(3只)", "电子布(3只)", "科技池(38只)", "创业板指"):
        assert name in out


def test_render_strip_within_width_limit():
    """悬浮窗横条空间有限，上限 40 个显示宽度单位。"""
    out = mp.render_strip(_state())
    assert mp.display_width(out) <= 40


def test_render_strip_contains_verdict():
    """2026-08-10 精简后单行只留池子涨跌幅、涨跌家数、判定，不再有排名。"""
    out = mp.render_strip(_state())
    assert "同涨" in out


def test_render_strip_when_unavailable():
    st = _state(status=("not_running", "不可用（悬浮窗未启动）"),
                verdict=(None, "样本不足（仅 18 只有效，需 20 只）"))
    out = mp.render_strip(st)
    assert "不可用" in out or "样本不足" in out
    assert mp.display_width(out) <= 40


# --- parse_pool_quotes / build_state --------------------------------------

def test_parse_pool_quotes_computes_r():
    raw = [{"code": "sz300308", "current": 977.45, "prev_close": 955.0, "ok": True}]
    got = mp.parse_pool_quotes(raw)
    assert got["sz300308"]["r"] == pytest.approx(2.3508, abs=1e-3)


def test_parse_pool_quotes_drops_suspended():
    """停牌（现价为 0）和昨收为 0 的票直接剔除，不列名、不提示。"""
    raw = [{"code": "a", "current": 0.0, "prev_close": 10.0, "ok": True},
           {"code": "b", "current": 10.0, "prev_close": 0.0, "ok": True},
           {"code": "c", "current": 11.0, "prev_close": 10.0, "ok": True}]
    got = mp.parse_pool_quotes(raw)
    assert set(got) == {"c"}


def test_parse_pool_quotes_drops_failed():
    raw = [{"code": "a", "current": None, "prev_close": None, "ok": False},
           {"code": "c", "current": 11.0, "prev_close": 10.0, "ok": True}]
    assert set(mp.parse_pool_quotes(raw)) == {"c"}


def test_parse_pool_quotes_uses_stock_watch_field_names():
    """入参字段名必须和 stock_watch.parse_sina_response 的真实输出一致。

    2026-08-07 踩过的坑：计划里写的入参键是 px，而真实字段是 current
    （stock_watch.py:75-78）。单测因为用自造数据而自洽通过，真跑起来
    每只票的 current 都取不到、整批被剔除，池子全空、判定全废。
    这条测试直接拿 parse_sina_response 的真实输出当输入，把契约钉死。
    """
    import stock_watch as sw
    raw = ('var hq_str_sz300308="中际旭创,981.09,955.00,977.45,999.88,963.00,'
           '977.40,977.45,20098011,19775573459,' + ','.join(["0"] * 20) + '";')
    got = mp.parse_pool_quotes(sw.parse_sina_response(raw))
    assert "sz300308" in got, "字段名对不上，真实行情会被整批剔除"
    assert got["sz300308"]["r"] == pytest.approx(2.3508, abs=1e-3)
    assert got["sz300308"]["px"] == pytest.approx(977.45)


def test_build_state_marks_all_windows_unavailable_when_not_running():
    """采集没跑起来时，三个窗口全部 None，不用任何别的数据源凑。"""
    st = mp.build_state([], mp.parse_ts("2026-08-07 13:25:00"), holdings=["sz300308"])
    assert st["status"][0] == "not_running"
    for row in st["rows"]:
        assert row["speeds"] == [None, None, None]


def _two_snaps():
    """构造两条快照：13:24:05 和 13:24:15，中际旭创从 +2.51 掉到 +2.30。

    间隔取 10 秒是为了落进最短窗口。窗口 10 秒时目标点是 13:24:05、
    容差 3.3 秒，隔 15 秒的快照够不着，speeds[0] 会是 None。
    """
    pool = {c: 1.0 for c in mp.POOL_CODES}
    a = dict(pool, sz300308=2.51, sz301526=7.00)
    b = dict(pool, sz300308=2.30, sz301526=7.24)
    return [{"t": "2026-08-07 13:24:05", "r": a, "idx": {"sz399006": 1.70}},
            {"t": "2026-08-07 13:24:15", "r": b, "idx": {"sz399006": 1.75}}]


def test_build_state_rows_in_spec_order():
    """行顺序：持仓票（3 行）→ 赛道（2 行）→ 科技池 → 创业板指 = 7 行。

    长鑫科技不在 SECTOR 里，不产生赛道行。
    """
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    names = [r["name"] for r in st["rows"]]
    assert names[:3] == ["中际旭创", "国际复材", "长鑫科技"]
    assert names[3].startswith("光模块") and names[4].startswith("电子布")
    assert names[5].startswith("科技池")
    assert names[6] == "创业板指"


def test_build_state_computes_10s_speed():
    """10 秒窗口：中际旭创从 +2.51 掉到 +2.30，速度 −0.21 pp。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"))
    assert st["rows"][0]["speeds"][0] == pytest.approx(-0.21)


def test_build_state_pool_outsider_has_no_speed():
    """长鑫科技不落盘，三个窗口全是 None。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    assert st["rows"][2]["speeds"] == [None, None, None]


def test_build_state_pool_outsider_still_gets_excess():
    """长鑫的相对强弱用实时行情出数，但排名为 None（不在池内）。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    cx = [h for h in st["holdings"] if h["name"] == "长鑫科技"][0]
    assert cx["r"] == pytest.approx(-0.17)
    assert cx["excess"] is not None
    assert cx["rank"][0] is None
    assert cx["in_pool"] is False


def test_build_state_breadth_uses_r_not_price():
    """宽度必须基于涨跌幅。

    2026-08-07 设计评审发现的缺陷：落盘若存价格，价格永远为正，
    上涨家数会恒等于 38。这条测试就是那个 bug 的守门人。
    """
    snaps = _two_snaps()
    snaps[-1]["r"]["sz300502"] = -3.0
    st = mp.build_state(snaps, mp.parse_ts("2026-08-07 13:24:15"))
    assert st["breadth"]["down"] >= 1
    assert st["breadth"]["up"] < len(mp.POOL_CODES)


def test_now_bj_returns_beijing_not_local():
    """必须返回北京时间。

    2026-08-07 实测：本机时区是 JST，比北京快 1 小时。若用本机时间
    比对 A 股时段，12 个时点里错 8 个——误采盘前，且尾盘一小时全漏。
    """
    import zoneinfo
    expect = datetime.datetime.now(
        zoneinfo.ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    assert abs((mp.now_bj() - expect).total_seconds()) < 5


def test_now_bj_is_naive():
    """去掉 tzinfo，好和落盘的 naive 时间戳直接相减。"""
    assert mp.now_bj().tzinfo is None


def test_in_session_boundary_is_beijing_clock():
    """时区 bug 的回归守护。

    北京 14:59 在场内、15:59 不在。本机 JST 下北京 14:59 就是本机 15:59，
    若误用本机时间会把尾盘整整一小时判成盘后、全部漏采。
    """
    assert mp.in_session("14:59") is True
    assert mp.in_session("15:59") is False


def test_parse_pool_quotes_rounds_r_once():
    """涨跌幅在 parse_pool_quotes 一次性定死 3 位小数，下游不再各自舍入。

    2026-08-07 实测踩到的坑：main() 落盘时 round(r,3)、live_r 用原始值，
    同一只票在 store 和 live_r 里取值不同，rank 比较时把自己也数了进去。
    """
    raw = [{"code": "a", "current": 977.45, "prev_close": 955.0, "ok": True}]
    assert mp.parse_pool_quotes(raw)["a"]["r"] == 2.351


def test_rank_never_exceeds_pool_size():
    """名次不能超过有效票数——防「排名 39/38（前 103%）」复现。"""
    raw = 2.350785340314141
    all_rs = [round(raw, 3)] + [1.0] * 37
    place, total = mp.rank(round(raw, 3), all_rs)
    assert place <= total
    assert place == 1


def test_build_state_rank_within_pool_size():
    """组装出来的持仓名次不能超过有效票数。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"))
    for h in st["holdings"]:
        place, total = h["rank"]
        if place is not None:
            assert place <= total, f"{h['name']} 名次 {place}/{total} 越界"


def test_build_state_rank_survives_precision_divergence():
    """store 与 live_r 精度不一致时，池内票的名次仍不能越界。

    2026-08-07 审查指出：上一版那条「端到端守护」让 live_r 走默认值
    （= store 的拷贝），精度天然一致，永远构造不出 39/38 的触发条件，
    等于打偏了靶心。这条显式喂进分叉输入——store 存舍入值 2.351、
    live_r 给原始值 2.3508（比 store 里自己那个值小）。
    修法是让池内票的 r 一律取 store 值，所以 live_r 再怎么分叉也影响不到名次。
    """
    raw = 2.350785340314141
    snaps = _two_snaps()
    snaps[-1]["r"]["sz300308"] = round(raw, 3)          # store：2.351
    st = mp.build_state(snaps, mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sz300308": raw})        # live：2.3508，偏小
    zj = [h for h in st["holdings"] if h["name"] == "中际旭创"][0]
    place, total = zj["rank"]
    assert place <= total, f"名次 {place}/{total} 越界——精度分叉又回来了"
    assert zj["r"] == round(raw, 3), "池内票的 r 必须取 store 值，不取 live_r"


def test_build_state_pool_outsider_still_uses_live_r():
    """池外票没进 store，它的 r 必须仍然从 live_r 取。

    上一条把池内票改成取 store 值，不能顺手把池外票也断了——
    长鑫科技不落盘，live_r 是它唯一的数据来源。
    """
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"),
                        live_r={"sh688825": -0.17})
    cx = [h for h in st["holdings"] if h["name"] == "长鑫科技"][0]
    assert cx["r"] == pytest.approx(-0.17)

def test_render_panel_shows_pool_level():
    """【宽度】节要给出池内整体涨跌幅。

    中位数和平均数都给：2026-08-10 13:45 实测两者差 0.56 个百分点
    （中位数 -3.18%、平均 -2.61%），去掉最强最弱各两只后中位数纹丝不动、
    平均数会跟着极端票跑。所以中位数放在前面，平均数并列供参照。
    """
    out = mp.render_panel(_state())
    assert "中位数" in out and "平均" in out
    assert "+1.66%" in out          # pool_median
    assert "+1.20%" in out          # pool_mean


def test_render_panel_pool_level_handles_none():
    """样本不足时中位数/平均数都是 None，不能格式化崩掉。"""
    out = mp.render_panel(_state(pool_median=None, pool_mean=None, valid=0))
    assert "【宽度】" in out


def test_render_strip_has_no_rank():
    """悬浮窗单行同样不再显示排名和超额。"""
    out = mp.render_strip(_state())
    assert "/38" not in out and "pp" not in out
    assert "同涨" in out
    assert mp.display_width(out) <= 40


def test_render_strip_shows_pool_and_counts():
    out = mp.render_strip(_state())
    assert "+1.66%" in out
    assert "34" in out and "4" in out


def test_build_state_provides_pool_mean():
    """build_state 要同时给出中位数和平均数。"""
    st = mp.build_state(_two_snaps(), mp.parse_ts("2026-08-07 13:24:15"))
    assert st["pool_median"] is not None
    assert st["pool_mean"] is not None


# --- 命令行参数 -----------------------------------------------------------

def test_parse_args_default_is_oneshot():
    assert mp.parse_args([]) is None


def test_parse_args_watch_default_interval():
    assert mp.parse_args(["--watch"]) == 15


def test_parse_args_watch_custom_interval():
    assert mp.parse_args(["--watch", "5"]) == 5


def test_parse_args_watch_rejects_garbage():
    """间隔不是正整数就抛，不静默用默认值——静默会让人以为设生效了。"""
    with pytest.raises(ValueError):
        mp.parse_args(["--watch", "abc"])
    with pytest.raises(ValueError):
        mp.parse_args(["--watch", "0"])


def test_parse_args_rejects_unknown():
    with pytest.raises(ValueError):
        mp.parse_args(["--nope"])


# --- 周末不采样 -----------------------------------------------------------

def test_append_store_skips_weekend(tmp_path):
    """周末休市不写盘。

    2026-08-10 实测踩到：in_session 只看 HH:MM 不看星期，
    周六周日在 9:30-11:30、13:00-15:00 照样判在场内，
    把周五收盘价的重复快照当实时数据写了 8045 条。
    """
    pp = tmp_path / "s.jsonl"
    sat = {"t": "2026-08-08 10:30:00", "r": {"sz300308": 2.35}, "idx": {}}
    sun = {"t": "2026-08-09 10:30:00", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(sat, path=str(pp)) is False
    assert mp.append_store(sun, path=str(pp)) is False
    assert not pp.exists()


def test_append_store_writes_weekday(tmp_path):
    """周一到周五的交易时段照常写。"""
    pp = tmp_path / "s.jsonl"
    mon = {"t": "2026-08-10 10:30:00", "r": {"sz300308": 2.35}, "idx": {}}
    assert mp.append_store(mon, path=str(pp)) is True


def test_is_trading_day():
    assert mp.is_trading_day("2026-08-10") is True     # 周一
    assert mp.is_trading_day("2026-08-07") is True     # 周五
    assert mp.is_trading_day("2026-08-08") is False    # 周六
    assert mp.is_trading_day("2026-08-09") is False    # 周日


def test_rotate_store_clears_any_stale_day(tmp_path):
    """文件里混了多天时全部清掉，不只看最后一行。

    2026-08-10 实测踩到：原实现只比对最后一行的日期，而进程跨日运行时
    最后一行恰好是今天，于是 8/8、8/9 两天的数据永远清不掉，
    文件从预估的每天 1.7 MB 涨到 9 MB。
    """
    pp = tmp_path / "s.jsonl"
    pp.write_text(
        '{"t":"2026-08-08 10:00:00","r":{},"idx":{}}\n'
        '{"t":"2026-08-09 10:00:00","r":{},"idx":{}}\n'
        '{"t":"2026-08-10 10:00:00","r":{},"idx":{}}\n', encoding="utf-8")
    assert mp.rotate_store("2026-08-10", path=str(pp)) is True
    left = [x for x in pp.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(left) == 1 and "2026-08-10" in left[0]
