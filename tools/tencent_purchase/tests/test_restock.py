from __future__ import annotations

import unittest

from tools.tencent_purchase.restock import has_shanghai_stock


class RestockTests(unittest.TestCase):
    def test_shanghai_quota_is_required(self) -> None:
        payload = {
            "code": 0,
            "data": [
                {
                    "available": 1,
                    "cycle_available": 1,
                    "quota": {
                        "4": {
                            "bundle_budget_mc_lg4_01": {
                                "available": 1,
                                "reserved": 0,
                            }
                        }
                    },
                }
            ],
        }
        self.assertTrue(
            has_shanghai_stock(
                payload,
                goods_type="bundle_budget_mc_lg4_01",
            )
        )
        payload["data"][0]["quota"]["4"]["bundle_budget_mc_lg4_01"][
            "reserved"
        ] = 1
        self.assertFalse(
            has_shanghai_stock(
                payload,
                goods_type="bundle_budget_mc_lg4_01",
            )
        )

    def test_non_success_codes_are_not_stock(self) -> None:
        self.assertFalse(
            has_shanghai_stock(
                {"code": "NOT-LOGINED", "data": [{"available": 1}]},
                goods_type="bundle_budget_mc_lg4_01",
            )
        )


if __name__ == "__main__":
    unittest.main()
