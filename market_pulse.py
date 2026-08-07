"""盘中涨跌速度监控 — 回答「我的票在跌，是独跌还是全市场同跌」。

设计文档：docs/market_pulse/spec/2026-08-07-market_pulse-spec.md

只描述已经发生的涨跌，不做方向预测。
"""
import os

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
