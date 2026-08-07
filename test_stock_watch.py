"""Unit tests for stock_watch pure functions (per spec test-case table)."""
import json

import stock_watch as sw


# --- normalize_code -----------------------------------------------------

def test_normalize_code_shanghai():
    assert sw.normalize_code("600519") == "sh600519"


def test_normalize_code_shenzhen():
    assert sw.normalize_code("000001") == "sz000001"


def test_normalize_code_chinext():
    # 创业板 300xxx 属深市
    assert sw.normalize_code("300750") == "sz300750"


def test_normalize_code_star_market():
    # 科创板 688xxx 属沪市
    assert sw.normalize_code("688981") == "sh688981"


def test_normalize_code_beijing():
    assert sw.normalize_code("830799") == "bj830799"


def test_normalize_code_strips_whitespace():
    assert sw.normalize_code("  600519 ") == "sh600519"


def test_normalize_code_alpha_is_us_ticker():
    """2026-07-31 加美股支持后契约变了：纯字母一律当美股代号。

    代价：无法离线校验美股代号是否真实存在，打错字（如 "abc"）会生成 gb_abc、
    取数时 ok=False、界面显示「—」，而不是像 A 股那样立刻报「无效代码」。
    """
    assert sw.normalize_code("abc") == "gb_abc"
    # 混合字母数字仍然非法
    assert sw.normalize_code("ab12") is None
    assert sw.normalize_code("") is None


def test_normalize_code_rejects_wrong_length():
    assert sw.normalize_code("12") is None
    assert sw.normalize_code("1234567") is None


def test_normalize_code_hongkong():
    # 5 位数字 -> 港股
    assert sw.normalize_code("02513") == "hk02513"
    assert sw.normalize_code("00700") == "hk00700"


def test_normalize_code_sh_etf():
    # 沪市基金/ETF: 5 开头
    assert sw.normalize_code("513120") == "sh513120"
    assert sw.normalize_code("510300") == "sh510300"


def test_normalize_code_sz_etf():
    # 深市基金/ETF/LOF: 1 开头
    assert sw.normalize_code("159915") == "sz159915"
    assert sw.normalize_code("161725") == "sz161725"


# --- parse_sina_response ------------------------------------------------

def _sina_line(code, name, prev_close, current):
    # 新浪字段: 名称,开盘,昨收,现价,...(后续字段本期用不到)
    return (
        f'var hq_str_{code}="{name},0.000,{prev_close},{current},'
        f'0.000,0.000,0.000,0.000,0,0.000,2026-06-29,13:47:26,00,";'
    )


def test_parse_single_quote():
    text = _sina_line("sh600519", "贵州茅台", "1168.630", "1195.010")
    result = sw.parse_sina_response(text)
    assert len(result) == 1
    q = result[0]
    assert q["code"] == "sh600519"
    assert q["name"] == "贵州茅台"
    assert q["ok"] is True
    # (1195.01-1168.63)/1168.63*100 = 2.2576... -> 2.26
    assert q["change_pct"] == 2.26


def test_parse_change_pct_simple():
    # 昨收 100, 现价 110 -> +10.00%
    text = _sina_line("sh600000", "测试股", "100.000", "110.000")
    q = sw.parse_sina_response(text)[0]
    assert q["change_pct"] == 10.00


def test_parse_negative_change():
    # 昨收 100, 现价 90 -> -10.00%
    text = _sina_line("sz000001", "跌股", "100.000", "90.000")
    q = sw.parse_sina_response(text)[0]
    assert q["change_pct"] == -10.00


def test_parse_suspended_current_zero():
    # 停牌: 现价为 0 -> ok=False
    text = _sina_line("sh600001", "停牌股", "10.000", "0.000")
    q = sw.parse_sina_response(text)[0]
    assert q["ok"] is False
    assert q["name"] == "停牌股"


def test_parse_empty_payload():
    # 无效代码新浪返回空串 -> ok=False，不崩溃
    text = 'var hq_str_sh999999="";'
    q = sw.parse_sina_response(text)[0]
    assert q["code"] == "sh999999"
    assert q["ok"] is False


def test_parse_hk_quote():
    # 港股字段不同：中文名在 [1]，昨收 [3]，现价 [6]
    text = (
        'var hq_str_hk02513="ZHIPU,智谱,0.000,100.000,0.000,0.000,'
        '110.000,10.000,10.000,0,0,0,0,0,0,0,2026/06/29,13:54";'
    )
    q = sw.parse_sina_response(text)[0]
    assert q["code"] == "hk02513"
    assert q["name"] == "智谱"
    assert q["ok"] is True
    assert q["change_pct"] == 10.00


def test_parse_multiple_quotes():
    text = "\n".join([
        _sina_line("sh600519", "贵州茅台", "1168.630", "1195.010"),
        _sina_line("sz000001", "平安银行", "10.000", "10.500"),
    ])
    result = sw.parse_sina_response(text)
    assert len(result) == 2
    assert result[0]["code"] == "sh600519"
    assert result[1]["code"] == "sz000001"
    assert result[1]["change_pct"] == 5.00


# --- config load/save ---------------------------------------------------

def test_config_roundtrip(tmp_path):
    path = tmp_path / "cfg.json"
    sw.save_config(["600519", "000001"], path=str(path))
    assert sw.load_config(path=str(path)) == ["600519", "000001"]


def test_config_missing_file_returns_empty(tmp_path):
    path = tmp_path / "does_not_exist.json"
    assert sw.load_config(path=str(path)) == []


def test_config_saved_as_json(tmp_path):
    path = tmp_path / "cfg.json"
    sw.save_config(["600519"], path=str(path))
    assert json.loads(path.read_text()) == ["600519"]


def test_config_default_path_in_script_dir():
    # 默认配置跟随脚本目录（随仓库走），不再放 home 目录
    import os
    assert os.path.dirname(sw.CONFIG_PATH) == os.path.dirname(os.path.abspath(sw.__file__))
    assert os.path.basename(sw.CONFIG_PATH) == "stock_watch.json"


def test_hk_quotes_parsed_from_tencent():
    """港股必须走腾讯接口解析：新浪的港股行情实测延迟十几分钟（2026-07-31
    中际旭创 H 股新浪报 1059/时间戳 10:22，腾讯同刻 1023，差 3.75 个百分点）。
    腾讯字段：f[1]=名称 f[3]=现价 f[4]=昨收（以 ~ 分隔）。"""
    text = ('v_hk03308="100~中际旭创~03308~1028.000~960.000~1104.000~3319193.0~0~0~1028.000";\n'
            'v_hk02513="100~智谱~02513~1060.000~862.000~991.000~5401788.0~0~0~1060.000";\n')
    qs = {q["code"]: q for q in sw.parse_tencent_hk_response(text)}
    assert qs["hk03308"]["name"] == "中际旭创"
    assert qs["hk03308"]["change_pct"] == round((1028.0 - 960.0) / 960.0 * 100, 2)
    assert qs["hk03308"]["ok"] is True
    assert qs["hk02513"]["change_pct"] == round((1060.0 - 862.0) / 862.0 * 100, 2)


def test_hk_quotes_invalid_when_no_data():
    """腾讯对未上市/停牌返回空或缺字段时，标记 ok=False 而不是给错数字。"""
    qs = sw.parse_tencent_hk_response('v_hk09999="";\n')
    assert qs and qs[0]["ok"] is False


# ---- 美股支持（含盘前）2026-07-31 ----

_US_SAMPLE = (
    'var hq_str_gb_mu="美光,874.6600,18.36,2026-07-31 17:08:08,135.6600,793.1350,'
    '882.5000,789.0000,1254.8500,102.8500,63075219,50456001,987541398838,44.80,'
    '19.520000,0.00,0.00,0.00,0.00,1129057461,85,914.5462,4.56,39.89,'
    'Jul 31 05:08AM EDT,Jul 30 04:00PM EDT,739.0000,1385323,1,2026";\n'
    'var hq_str_gb_skhy="SK海力士,149.0000,17.52,2026-07-31 17:08:02,22.2100,136.5100,'
    '150.3560,134.5000,194.8000,124.8000,52881195,46086041,1086061000000,0.00,--,'
    '0.00,0.00,0.00,0.00,7289000000,0,160.1000,7.45,0.00,'
    'Jul 31 05:08AM EDT,Jul 30 04:00PM EDT,126.7800,0,1,2026";\n'
)


def test_us_quotes_parsed_from_sina_gb():
    """美股走新浪 gb_ 接口。f[1]/f[2] 是**上一个正常时段的收盘价与收盘涨跌幅**。
    实测校验：美光 874.66 / 昨收 739.00 = +18.36%，与接口 f[2] 一致。"""
    qs = {q["code"]: q for q in sw.parse_sina_us_response(_US_SAMPLE)}
    assert qs["gb_mu"]["name"] == "美光"
    assert qs["gb_mu"]["change_pct"] == 18.36
    assert qs["gb_mu"]["ok"] is True
    assert qs["gb_skhy"]["name"] == "SK海力士"
    assert qs["gb_skhy"]["change_pct"] == 17.52


def test_us_quotes_invalid_when_no_data():
    """无数据/停牌时标记 ok=False，不给错数字。"""
    qs = sw.parse_sina_us_response('var hq_str_gb_zzzz="";\n')
    assert qs and qs[0]["ok"] is False


def test_normalize_code_supports_us():
    """用户输入 MU / mu / SKHY 应归一成 gb_ 前缀。"""
    assert sw.normalize_code("MU") == "gb_mu"
    assert sw.normalize_code("sndk") == "gb_sndk"
    assert sw.normalize_code("SKHY") == "gb_skhy"


def test_us_premarket_realtime_pct():
    """盘前要显示的是**真正实时的涨跌幅**，不是振幅。

    2026-07-31 实测踩坑：f[1]/f[2] 是**上一个正常交易时段的收盘价与收盘涨跌幅**
    （腾讯同源时间戳明确写 2026-07-30 16:00:01），把它当盘前价是错的。
    真正实时的盘前数据在：f[21]=盘前价 f[22]=盘前涨跌幅% f[23]=盘前涨跌额。
    50 秒两次取数验证过三只全部跳动（美光 +4.33%→+4.46%）。
    另外 f[24] 是**查询时间**、f[25] 才是**成交时间**，这两个字段易搞反。
    """
    qs = {q["code"]: q for q in sw.parse_sina_us_response(_US_SAMPLE)}
    mu = qs["gb_mu"]
    assert mu["change_pct"] == 18.36          # 收盘涨跌幅
    assert mu["close"] == 874.66              # 收盘价
    assert mu["ext_pct"] == 4.56              # 盘前实时涨跌幅 f[22]
    assert mu["ext_price"] == 914.5462        # 盘前价 f[21]
    assert mu["quote_time"] == "Jul 30 04:00PM EDT"   # f[25] 成交时间
    # 914.5462 / 874.66 - 1 = +4.56%，与 f[22] 自洽
    assert round(mu["ext_price"] / mu["close"] * 100 - 100, 2) == mu["ext_pct"]


def test_us_session_uses_trade_time_not_query_time():
    """时段判断必须用成交时间 f[25]，不能用查询时间 f[24]——否则会把周四收盘
    的数据标成「盘前」（2026-07-31 实际犯过这个错）。"""
    qs = {q["code"]: q for q in sw.parse_sina_us_response(_US_SAMPLE)}
    assert qs["gb_mu"]["session"] == "收盘"    # f[25]=Jul 30 04:00PM EDT
    assert sw._us_session("Jul 31 10:30AM EDT") == "盘中"
    assert sw._us_session("Jul 31 06:30PM EDT") == "盘后"


def test_us_ext_pct_none_when_absent():
    """无盘前数据时 ext_pct 给 None，不编造、不拿收盘价冒充。"""
    qs = sw.parse_sina_us_response('var hq_str_gb_x="X,10.0,1.5";\n')
    assert qs[0]["ok"] is True and qs[0]["ext_pct"] is None


def test_us_ext_label_by_current_et_clock():
    """ext_pct 字段本身不区分盘前/盘后（两个时段共用 f[21]/f[22]），标签只能
    由当前美东时钟给。正常交易时段返回空串——那时 f[1] 就是实时价，不必显示 ext。"""
    import datetime, zoneinfo
    et = zoneinfo.ZoneInfo("America/New_York")
    def at(h, m):
        return datetime.datetime(2026, 7, 31, h, m, tzinfo=et)
    assert sw._us_ext_label(at(5, 19)) == "前"
    assert sw._us_ext_label(at(10, 30)) == ""      # 盘中不显示 ext
    assert sw._us_ext_label(at(17, 0)) == "后"
    assert sw._us_ext_label(at(2, 0)) == ""        # 盘前开始前


# --- 市场脉搏接入（Task 10）-------------------------------------------------

def test_watch_codes_merge_includes_pool():
    """悬浮窗请求的代码表里必须含 38 只池子票，否则采集拿不到数据。"""
    import market_pulse as mp
    import stock_watch as sw
    merged = mp.merge_codes(sw.load_config())
    for code in mp.POOL_CODES:
        assert code in merged


def test_watch_codes_has_no_duplicates():
    import market_pulse as mp
    import stock_watch as sw
    merged = mp.merge_codes(sw.load_config())
    assert len(merged) == len(set(merged))
