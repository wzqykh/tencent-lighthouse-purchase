from __future__ import annotations

import unittest
from decimal import Decimal

from tools.tencent_purchase.checks import (
    classify_cta,
    explicit_submit_failure,
    extract_real_total,
    reached_payment_stage,
    region_candidates,
    validate_card_text,
    validate_order_text,
    validate_total,
)


class CheckTests(unittest.TestCase):
    def test_valid_card_and_cta(self) -> None:
        text = (
            "轻量应用服务器 4核4G3M 300GB 月流量 活动价： 38 元 "
            "日常价：780元"
        )
        self.assertEqual(validate_card_text(text), [])
        self.assertEqual(classify_cta("立即抢购", False), "active")
        self.assertEqual(classify_cta("立即抢购", True), "unavailable")
        self.assertEqual(classify_cta("添加提醒", False), "presale")

    def test_wrong_price_is_rejected(self) -> None:
        text = "轻量应用服务器 4核4G3M 300GB 月流量 活动价： 39 元 日常价：780元"
        self.assertTrue(validate_card_text(text))

    def test_order_text_and_total(self) -> None:
        text = (
            "轻量应用服务器 4核4G3M 40GB SSD 300GB 1年 "
            "Linux类 北京 实付金额 38.00 元"
        )
        self.assertEqual(validate_order_text(text, region="北京"), [])
        self.assertEqual(extract_real_total(text), Decimal("38.00"))
        self.assertTrue(validate_total(Decimal("38.00")))
        self.assertFalse(validate_total(Decimal("39.00")))

    def test_submit_result_classification(self) -> None:
        self.assertTrue(explicit_submit_failure("订单提交失败，请稍后重试"))
        self.assertTrue(reached_payment_stage("支付订单-腾讯云", "确认支付"))
        self.assertFalse(reached_payment_stage("确认产品信息-腾讯云", "去支付"))

    def test_region_candidates_keep_available_default_first(self) -> None:
        self.assertEqual(
            region_candidates("上海"),
            ("上海", "北京", "广州"),
        )
        self.assertEqual(
            region_candidates(""),
            ("北京", "上海", "广州"),
        )


if __name__ == "__main__":
    unittest.main()
