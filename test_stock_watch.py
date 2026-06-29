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


def test_normalize_code_rejects_non_digits():
    assert sw.normalize_code("abc") is None


def test_normalize_code_rejects_wrong_length():
    assert sw.normalize_code("12") is None
    assert sw.normalize_code("1234567") is None


def test_normalize_code_hongkong():
    # 5 位数字 -> 港股
    assert sw.normalize_code("02513") == "hk02513"
    assert sw.normalize_code("00700") == "hk00700"


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
