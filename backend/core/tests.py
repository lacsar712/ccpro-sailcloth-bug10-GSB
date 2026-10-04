"""跨间同卷码隔离的验收测试。

场景：甲间、乙间各有一卷 R-01。点选、近次、标已固化都必须只跟
当前间这一卷走，不许只拿卷码字符串跨间匹配。
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from .models import ClothRoll, DipRun, Loft

User = get_user_model()


class CrossLoftIsolationTests(APITestCase):
    def setUp(self):
        self.worker_a = User.objects.create_user(
            username="worker_a", password="x", role=User.ROLE_WORKER
        )
        self.worker_b = User.objects.create_user(
            username="worker_b", password="x", role=User.ROLE_WORKER
        )
        self.loft_a = Loft.objects.create(name="甲间")
        self.loft_b = Loft.objects.create(name="乙间")
        self.roll_a = ClothRoll.objects.create(
            loft=self.loft_a,
            roll_code="R-01",
            status=ClothRoll.STATUS_DIPPING,
            fabric_weight_gsm=420,
            notes="甲间的卷",
        )
        self.roll_b = ClothRoll.objects.create(
            loft=self.loft_b,
            roll_code="R-01",
            status=ClothRoll.STATUS_DIPPING,
            fabric_weight_gsm=390,
            notes="乙间的卷",
        )
        self.client.force_authenticate(self.worker_a)

    def _dip(self, roll, hours_ago, resin, cure_hours):
        return DipRun.objects.create(
            roll=roll,
            started_at=timezone.now() - timedelta(hours=hours_ago),
            resin_pct=Decimal(str(resin)),
            cure_hours=None if cure_hours is None else Decimal(str(cure_hours)),
        )

    def _patch_roll(self, client, roll, payload):
        return client.patch(f"/api/rolls/{roll.id}/", payload, format="json")

    # --- 近次列表：按当前卷的主键过滤，不串乙间同码卷 ---
    def test_dip_list_filters_by_roll_id_not_roll_code(self):
        self._dip(self.roll_a, 5, "28.50", None)
        self._dip(self.roll_b, 3, "22.00", "8.00")

        resp = self.client.get(f"/api/dips/?rollId={self.roll_a.id}")

        self.assertEqual(resp.status_code, 200)
        rows = resp.data.get("results", resp.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rollId"], self.roll_a.id)
        self.assertEqual(rows[0]["resinPct"], "28.50")
        self.assertEqual(rows[0]["loftName"], "甲间")

    # --- 固化门槛：只读本间这一卷的时长，不许借乙间的 14h 过关 ---
    def test_cure_validation_uses_own_loft_dip_hours(self):
        self._dip(self.roll_a, 8, "28.50", "8.00")  # 甲间最近仅 8h，不够
        self._dip(self.roll_b, 20, "22.00", "14.00")  # 乙间同码卷 14h，够

        resp = self._patch_roll(self.client, self.roll_a, {"status": "cured"})

        self.assertEqual(resp.status_code, 400)
        self.roll_a.refresh_from_db()
        self.assertEqual(self.roll_a.status, ClothRoll.STATUS_DIPPING)

    # --- 甲间固化成功：只改甲间这卷，乙间那卷一律不许动 ---
    def test_cure_success_does_not_touch_other_loft_roll(self):
        self._dip(self.roll_a, 20, "28.50", "13.00")
        self._dip(self.roll_b, 18, "22.00", "14.00")

        resp = self._patch_roll(
            self.client, self.roll_a, {"status": "cured", "notes": "甲间已固化"}
        )

        self.assertEqual(resp.status_code, 200)
        self.roll_a.refresh_from_db()
        self.roll_b.refresh_from_db()
        self.assertEqual(self.roll_a.status, ClothRoll.STATUS_CURED)
        self.assertEqual(self.roll_a.notes, "甲间已固化")
        self.assertEqual(self.roll_b.status, ClothRoll.STATUS_DIPPING)
        self.assertEqual(self.roll_b.fabric_weight_gsm, 390)
        self.assertEqual(self.roll_b.notes, "乙间的卷")

    # --- 两名浸胶工交叉点标已固化：各自只改自己这一间 ---
    def test_two_workers_cure_their_own_loft_only(self):
        self._dip(self.roll_a, 20, "28.50", "13.00")
        self._dip(self.roll_b, 18, "22.00", "15.00")
        client_a = APIClient()
        client_a.force_authenticate(self.worker_a)
        client_b = APIClient()
        client_b.force_authenticate(self.worker_b)

        resp_a = self._patch_roll(
            client_a, self.roll_a, {"status": "cured", "notes": "甲间已固化"}
        )
        resp_b = self._patch_roll(
            client_b, self.roll_b, {"status": "cured", "notes": "乙间已固化"}
        )

        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_b.status_code, 200)
        self.roll_a.refresh_from_db()
        self.roll_b.refresh_from_db()
        self.assertEqual(self.roll_a.status, ClothRoll.STATUS_CURED)
        self.assertEqual(self.roll_b.status, ClothRoll.STATUS_CURED)
        # 写入落在各自那一卷上，没有互换
        self.assertEqual(self.roll_a.notes, "甲间已固化")
        self.assertEqual(self.roll_b.notes, "乙间已固化")
        self.assertEqual(self.roll_a.loft_id, self.loft_a.id)
        self.assertEqual(self.roll_b.loft_id, self.loft_b.id)
        self.assertEqual(self.roll_a.fabric_weight_gsm, 420)
        self.assertEqual(self.roll_b.fabric_weight_gsm, 390)

    # --- 乙间自己时长不够时：不许借甲间的时长，也不许改写甲间的卷 ---
    def test_worker_cannot_borrow_other_loft_hours(self):
        self._dip(self.roll_a, 20, "28.50", "13.00")  # 甲间够
        self._dip(self.roll_b, 3, "22.00", "8.00")  # 乙间自己不够
        client_b = APIClient()
        client_b.force_authenticate(self.worker_b)

        resp = self._patch_roll(client_b, self.roll_b, {"status": "cured"})

        self.assertEqual(resp.status_code, 400)
        self.roll_a.refresh_from_db()
        self.roll_b.refresh_from_db()
        self.assertEqual(self.roll_a.status, ClothRoll.STATUS_DIPPING)
        self.assertEqual(self.roll_b.status, ClothRoll.STATUS_DIPPING)
