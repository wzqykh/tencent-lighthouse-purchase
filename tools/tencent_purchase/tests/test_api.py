from __future__ import annotations

import datetime as dt
import unittest
from decimal import Decimal

from tools.tencent_purchase.api import (
    ApiDisposition,
    TargetOffer,
    activity_id_from_requests,
    build_do_goods_payload,
    classify_do_goods_response,
    csrf_token_from_skey,
    target_offer_from_schedule,
)
from tools.tencent_purchase.schedule import CHINA_TZ


def schedule_payload(*, real_cost: int = 3800) -> dict[str, object]:
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "nowTime": 1793095903076,
            "flashResult": {
                "2026-10-28": [
                    {
                        "id": 25055,
                        "start_date": "2026-10-28",
                        "start_time": "10:00:00",
                        "sales": [
                            {
                                "act_id": 1996855673581677,
                                "goodsId": 1996855673581677,
                                "activityId": 166111704417621,
                                "product_name": "lighthouse_v5",
                                "$goods": {
                                    "act_id": 1996855673581677,
                                    "activity_id": 166111704417621,
                                },
                                "defaultOrderParam": {
                                    "BlueprintId": "LINUX_UNIX",
                                    "area": 1,
                                    "ddocUnionConnect": 0,
                                    "goodsNum": 1,
                                    "imageId": "lhbp-eqora508",
                                    "scenario": "0",
                                    "timeSpanUnit": "12m",
                                    "zone": "",
                                    "regionId": 4,
                                    "type": "bundle_budget_mc_lg4_01",
                                },
                                "priceData": {
                                    "real_cost": real_cost,
                                    "total_cost": 78000,
                                },
                            }
                        ],
                    }
                ]
            },
        },
    }


class ApiTests(unittest.TestCase):
    def test_csrf_matches_activity_frontend_algorithm(self) -> None:
        self.assertEqual(csrf_token_from_skey("abc"), "193485963")

    def test_activity_id_is_derived_from_observed_requests(self) -> None:
        requests = [
            {
                "post_data": (
                    '{"activity_id":166111704417621,"preview":0}'
                )
            },
            {
                "post_data": (
                    '{"activity_id":166111704417621,"goods":[],"preview":1}'
                )
            },
            {"post_data": "not-json"},
        ]
        self.assertEqual(activity_id_from_requests(requests), 166111704417621)

    def test_schedule_selects_only_exact_sale_and_verifies_price(self) -> None:
        start = dt.datetime(2026, 10, 28, 10, 0, tzinfo=CHINA_TZ)
        offer = target_offer_from_schedule(schedule_payload(), session_start=start)
        self.assertEqual(offer.activity_id, 166111704417621)
        self.assertEqual(offer.session_id, 25055)
        self.assertEqual(offer.goods_id, 1996855673581677)
        self.assertEqual(offer.goods_param["regionId"], 4)
        self.assertEqual(offer.total, Decimal("38.00"))

    def test_schedule_rejects_changed_price(self) -> None:
        start = dt.datetime(2026, 10, 28, 10, 0, tzinfo=CHINA_TZ)
        with self.assertRaisesRegex(ValueError, "real_cost"):
            target_offer_from_schedule(
                schedule_payload(real_cost=3900),
                session_start=start,
            )

    def test_order_payload_is_one_shanghai_submission(self) -> None:
        offer = TargetOffer(
            activity_id=166111704417621,
            session_id=25055,
            goods_id=1996855673581677,
            product_type="lighthouse_v5",
            goods_param={
                "BlueprintId": "LINUX_UNIX",
                "area": 1,
                "ddocUnionConnect": 0,
                "goodsNum": 1,
                "imageId": "lhbp-eqora508",
                "scenario": "0",
                "timeSpanUnit": "12m",
                "zone": "",
                "regionId": 4,
                "type": "bundle_budget_mc_lg4_01",
            },
            total=Decimal("38.00"),
            server_now_ms=1793095903076,
        )
        payload = build_do_goods_payload(
            offer,
            page_url="https://cloud.tencent.com/act/pro/double11-202611",
        )
        self.assertEqual(payload["activity_id"], 166111704417621)
        self.assertEqual(payload["business"], {"id": 25055, "from": "lightningDeals"})
        goods = payload["goods"]
        self.assertIsInstance(goods, list)
        self.assertEqual(len(goods), 1)
        self.assertEqual(goods[0]["goods_param"]["regionId"], 4)
        self.assertEqual(goods[0]["goods_param"]["goodsNum"], 1)
        self.assertNotIn("pay", payload)

    def test_result_classification_is_conservative(self) -> None:
        created = classify_do_goods_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {"big_deal_no": ["202610280001"], "deal_names": ["x"]},
            }
        )
        self.assertEqual(created.disposition, ApiDisposition.ORDER_CREATED)
        self.assertEqual(created.deal_id, "202610280001")
        self.assertEqual(
            classify_do_goods_response({"code": 1101001, "msg": "秒杀尚未开始"}).disposition,
            ApiDisposition.NOT_STARTED,
        )
        self.assertEqual(
            classify_do_goods_response({"code": 1, "msg": "商品已售罄"}).disposition,
            ApiDisposition.SOLD_OUT,
        )
        self.assertEqual(
            classify_do_goods_response({"code": "NOT-LOGINED"}).disposition,
            ApiDisposition.NOT_LOGGED_IN,
        )
        self.assertEqual(
            classify_do_goods_response({"code": "CSRF-ERROR"}).disposition,
            ApiDisposition.CSRF_ERROR,
        )
        self.assertEqual(
            classify_do_goods_response(
                {"code": "408", "msg": "系统访问频率过快，请稍后再试"}
            ).disposition,
            ApiDisposition.RATE_LIMITED,
        )
        self.assertEqual(
            classify_do_goods_response({"code": 0, "msg": "ok"}).disposition,
            ApiDisposition.UNKNOWN,
        )
        self.assertEqual(
            classify_do_goods_response({"code": 999999, "msg": "other"}).disposition,
            ApiDisposition.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
