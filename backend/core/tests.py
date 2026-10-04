"""跨帆布间同卷码隔离回归测试。

验收口径：点选、近次、能不能固化都只跟当前间这一卷走，
不得只拿卷码字符串跨间匹配；甲间固化成功不许改乙间那卷。
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import ClothRoll, DipRun, Loft

User = get_user_model()


class CrossLoftIsolationTests(APITestCase):
    """甲间、乙间各有一卷 R-01，所有操作必须各自落在本间这卷上。"""

    def setUp(self):
        self.worker = User.objects.create_user(username="worker_a", password="x")
        self.client.force_authenticate(self.worker)

        self.loft_a = Loft.objects.create(name="甲间")
        self.loft_b = Loft.objects.create(name="乙间")
        self.roll_a = ClothRoll.objects.create(
            loft=self.loft_a, roll_code="R-01", status=ClothRoll.STATUS_DIPPING
        )
        self.roll_b = ClothRoll.objects.create(
            loft=self.loft_b, roll_code="R-01", status=ClothRoll.STATUS_DIPPING
        )

    def _dip(self, roll, hours_ago, cure_hours):
        return DipRun.objects.create(
            roll=roll,
            started_at=timezone.now() - timedelta(hours=hours_ago),
            resin_pct=Decimal("28.00"),
            cure_hours=cure_hours,
        )

    def test_dip_list_filters_by_roll_id_not_roll_code(self):
        """近次列表：按 rollId 查询只回本间这卷的浸渍，不串乙间同码卷。"""
        dip_a = self._dip(self.roll_a, hours_ago=5, cure_hours=None)
        self._dip(self.roll_b, hours_ago=3, cure_hours=Decimal("8.00"))

        resp = self.client.get("/api/dips/", {"rollId": self.roll_a.id})

        self.assertEqual(resp.status_code, 200)
        ids = [row["id"] for row in resp.data["results"]]
        self.assertEqual(ids, [dip_a.id])

    def test_cure_check_reads_own_roll_dips_only(self):
        """能不能固化：乙间同码卷时长再够，也救不了甲间本卷时长不足。"""
        self._dip(self.roll_b, hours_ago=30, cure_hours=Decimal("14.00"))
        self._dip(self.roll_a, hours_ago=2, cure_hours=Decimal("4.00"))

        resp = self.client.patch(
            f"/api/rolls/{self.roll_a.id}/", {"status": "cured"}, format="json"
        )

        self.assertEqual(resp.status_code, 400)
        self.roll_a.refresh_from_db()
        self.assertEqual(self.roll_a.status, ClothRoll.STATUS_DIPPING)

    def test_cure_success_does_not_touch_twin_roll(self):
        """甲间固化成功只改甲间这卷，乙间同码卷原样不动。"""
        self._dip(self.roll_a, hours_ago=20, cure_hours=Decimal("13.00"))
        self._dip(self.roll_b, hours_ago=3, cure_hours=Decimal("8.00"))

        resp = self.client.patch(
            f"/api/rolls/{self.roll_a.id}/", {"status": "cured"}, format="json"
        )

        self.assertEqual(resp.status_code, 200)
        self.roll_a.refresh_from_db()
        self.roll_b.refresh_from_db()
        self.assertEqual(self.roll_a.status, ClothRoll.STATUS_CURED)
        self.assertEqual(self.roll_b.status, ClothRoll.STATUS_DIPPING)

    def test_two_workers_cure_their_own_lofts_roll(self):
        """两名浸胶工交叉点标已固化：各自只改自己那一间的 R-01。"""
        self._dip(self.roll_a, hours_ago=20, cure_hours=Decimal("13.00"))
        self._dip(self.roll_b, hours_ago=22, cure_hours=Decimal("15.00"))
        worker_b = User.objects.create_user(username="worker_b", password="x")

        resp_a = self.client.patch(
            f"/api/rolls/{self.roll_a.id}/", {"status": "cured"}, format="json"
        )
        self.client.force_authenticate(worker_b)
        resp_b = self.client.patch(
            f"/api/rolls/{self.roll_b.id}/", {"status": "cured"}, format="json"
        )

        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_b.status_code, 200)
        self.roll_a.refresh_from_db()
        self.roll_b.refresh_from_db()
        self.assertEqual(self.roll_a.status, ClothRoll.STATUS_CURED)
        self.assertEqual(self.roll_b.status, ClothRoll.STATUS_CURED)
