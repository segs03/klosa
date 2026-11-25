"""Updates your four OLD test files to the new ID rules. Run it from your project folder:

    python bank_changes/apply_test_fixes.py

It only changes lines it recognises, says what it did, and is safe to run twice.
"""
import pathlib

IND = " " * 12

FIXES = {
    "accounts/tests/test_bonus.py": [
        # a NIN alone no longer upgrades the account; only the BVN does
        ('self.assertEqual(self.acc.kyc_tier, 1)',
         'self.assertEqual(self.acc.kyc_tier, 0)          # a NIN alone no longer upgrades; only the BVN does'),
        # BVN is checked from the number only, so the photo test uses a passport
        ('self.assertEqual(submit_application(self.u, doc_type="bvn", document=SimpleUploadedFile("a.jpg", JPG), **kw).document_type, "bvn")',
         'self.assertEqual(submit_application(self.u, doc_type="passport", document=SimpleUploadedFile("a.jpg", JPG), **kw).document_type, "passport")\n'
         + IND + 'with self.assertRaises(ValidationError):          # a BVN can no longer be sent as a photo\n'
         + IND + '    submit_application(self.u, doc_type="bvn", document=SimpleUploadedFile("c.jpg", JPG), **kw)'),
    ],
    "accounts/tests/test_flow.py": [
        # the page now has three steps: BVN, one more ID, address
        ('self.assertEqual([s["state"] for s in r.context["steps"]], ["done", "todo"])',
         'self.assertEqual([s["state"] for s in r.context["steps"]], ["todo", "done", "todo"])'),
    ],
    "accounts/tests/test_kyc.py": [
        ('self.assertEqual((a.status, self.acc.kyc_tier), ("verified", 1))',
         'self.assertEqual((a.status, self.acc.kyc_tier), ("verified", 0))   # a NIN alone no longer upgrades'),
        ('    def test_rejection_and_requests_for_more_information(self):',
         '    def test_a_bvn_approval_raises_the_tier(self):\n'
         '        app = KYCApplication.objects.create(\n'
         '            user=self.user, country_code="NG", document_type="bvn", legal_name="Ada Obi",\n'
         '            date_of_birth=date(1990, 1, 1), identity_document="kyc/x.jpg",\n'
         '            identity_digest=id_digest("bvn", "12345678901"))\n'
         '        review_application(app.pk, self.staff(), "approved", "ok")\n'
         '        self.acc.refresh_from_db()\n'
         '        self.assertEqual(self.acc.kyc_tier, 1)\n'
         '\n'
         '    def test_rejection_and_requests_for_more_information(self):'),
        ('self.assertIn("BVN", self.msg(doc_type="bvn", id_number="12"))',
         'self.assertIn("number only", self.msg(doc_type="bvn", id_number="12"))'),
    ],
    "accounts/tests/test_tiers.py": [
        ('from accounts.models import Account, KYCApplication, VerifiedDocument',
         'from accounts.models import Account, AddressProof, KYCApplication, VerifiedDocument'),
        ('class TierTests(TestCase):',
         'def approved_address(user):\n'
         '    return AddressProof.objects.create(user=user, line1="12 Admiralty Way", city="Lagos", state="Lagos",\n'
         '                                       bill_date=date.today(), document="address/x.jpg", status="approved")\n'
         '\n\n'
         'class TierTests(TestCase):'),
        ('    def test_both_ids_and_a_face_make_tier_three(self):',
         '    def test_both_ids_and_a_face_make_tier_three(self):\n'
         '        approved_address(self.u)'),
        ('    def test_a_face_alone_changes_nothing(self):',
         '    def test_without_an_approved_address_the_top_tier_is_not_reached(self):\n'
         '        doc(self.u, "nin")\n'
         '        doc(self.u, "bvn")\n'
         '        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())\n'
         '        refresh_tier(self.u)\n'
         '        self.assertEqual(self.tier(), 2)\n'
         '\n'
         '    def test_a_nin_alone_does_not_upgrade(self):\n'
         '        doc(self.u, "nin")\n'
         '        refresh_tier(self.u)\n'
         '        self.assertEqual(self.tier(), 1)\n'
         '\n'
         '    def test_a_passport_works_as_the_second_id(self):\n'
         '        doc(self.u, "bvn")\n'
         '        doc(self.u, "passport")\n'
         '        approved_address(self.u)\n'
         '        Account.objects.filter(pk=self.acc.pk).update(face_verified_at=timezone.now())\n'
         '        refresh_tier(self.u)\n'
         '        self.assertEqual(self.tier(), 3)\n'
         '\n'
         '    def test_a_face_alone_changes_nothing(self):'),
    ],
}

for path, pairs in FIXES.items():
    p = pathlib.Path(path)
    if not p.exists():
        print(f"MISSING  {path}  (run this from your project folder)")
        continue
    text = p.read_text()
    for old, new in pairs:
        if new in text:
            print(f"already  {path}: {old[:50]!r}")
        elif text.count(old) != 1:
            print(f"SKIPPED  {path}: {old[:50]!r} (found {text.count(old)} times)")
        else:
            text = text.replace(old, new)
            print(f"fixed    {path}: {old[:50]!r}")
    p.write_text(text)
