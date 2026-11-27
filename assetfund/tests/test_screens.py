from django.test import Client, override_settings
from django.urls import reverse

from assetfund.models import AssetApplication

from .helpers import CFG, STAGES, AssetCase, make_user


def as_user(user):
    client = Client()
    client.force_login(user)
    return client


FORM = dict(kind="gold", title="Raw gold for jewellery", asset_ref="ASSAY-1001",
            description="Twenty four carat gold bars held in the vault", declared_value="1000000",
            requested_amount="400000", purpose="Mould the gold into jewellery for a wedding order")


@override_settings(**CFG)
class ScreenTests(AssetCase):
    def test_login_and_staff_rules(self):
        self.assertEqual(Client().get(reverse("assetfund:home")).status_code, 302)
        self.assertEqual(as_user(self.cust).get(reverse("assetfund:staff")).status_code, 403)
        self.assertEqual(as_user(make_user("plain", staff=True)).get(reverse("assetfund:staff")).status_code, 403)

    def test_customer_pages_render(self):
        c = as_user(self.cust)
        self.assertEqual(c.get(reverse("assetfund:home")).status_code, 200)
        self.assertEqual(c.get(reverse("assetfund:apply")).status_code, 200)

    def test_applying_and_a_bad_application(self):
        c = as_user(self.cust)
        bad = c.post(reverse("assetfund:apply"), dict(FORM, requested_amount="abc"))
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(AssetApplication.objects.count(), 0)
        ok = c.post(reverse("assetfund:apply"), FORM)
        self.assertEqual(ok.status_code, 302)
        app = AssetApplication.objects.get()
        self.assertEqual(c.get(reverse("assetfund:detail", args=[app.pk])).status_code, 200)

    def test_nobody_else_can_open_my_application(self):
        app = self.apply()
        self.assertEqual(as_user(make_user("thief")).get(reverse("assetfund:detail", args=[app.pk])).status_code, 404)

    def test_the_whole_flow_through_the_screens(self):
        c = as_user(self.cust)
        c.post(reverse("assetfund:apply"), FORM)
        app = AssetApplication.objects.get()
        mine, staff = reverse("assetfund:detail", args=[app.pk]), reverse("assetfund:staff_detail", args=[app.pk])

        data = {"action": "assess", "verified_value": "900000", "advance_rate": "40", "fee_pct": "10", "share_pct": "50",
                "term_days": "180", "note": "Inspected and assayed, 24 carat, 450g"}
        for i, (title, amount) in enumerate(STAGES, start=1):
            data[f"stage_title_{i}"], data[f"stage_amount_{i}"] = title, amount
        assessor, approver, teller = as_user(self.assessor), as_user(self.approver), as_user(self.teller)
        self.assertEqual(assessor.get(reverse("assetfund:staff")).status_code, 200)
        self.assertEqual(assessor.get(staff).status_code, 200)
        assessor.post(staff, data)
        approver.post(staff, {"action": "approved", "reason": "Value confirmed"})
        self.assertEqual(c.get(mine).status_code, 200)
        c.post(mine, {"action": "accept", "pin": "1234"})
        app.refresh_from_db()
        self.assertEqual(app.status, "active")

        stage = app.stages.get(order=1)
        teller.post(staff, {"action": "release", "stage": stage.pk})
        self.assertEqual(self.balance(), 100000)
        self.assertEqual(c.get(mine).status_code, 200)
        self.assertEqual(teller.get(staff).status_code, 200)

        c.post(mine, {"action": "evidence", "stage": app.stages.get(order=2).pk, "note": "Moulds bought, photos kept"})
        c.post(mine, {"action": "sale", "proceeds": "50000", "note": "Sold two necklaces to a retailer"})
        self.assertEqual(app.sales.count(), 1)
        self.assertEqual(teller.get(staff).status_code, 200)

    def test_a_staff_member_without_the_permission_cannot_act(self):
        app = self.apply()
        as_user(self.approver).post(reverse("assetfund:staff_detail", args=[app.pk]), {"action": "assess", "verified_value": "900000"})
        app.refresh_from_db()
        self.assertEqual(app.status, "pending")

    def test_bad_input_on_the_staff_page_does_not_crash(self):
        app = self.apply()
        url = reverse("assetfund:staff_detail", args=[app.pk])
        t = as_user(self.assessor)
        self.assertEqual(t.post(url, {"action": "assess", "term_days": "abc"}).status_code, 302)
        self.assertEqual(t.post(url, {"action": "release", "stage": "x"}).status_code, 302)
        self.assertEqual(t.post(url, {"action": "nonsense"}).status_code, 302)
