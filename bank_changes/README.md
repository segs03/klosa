# Klosa changes: address proof, new ID rules, credit-card payments, ledger, tests

## Install on Termux
```
cd /storage/emulated/0/bank
unzip -o /storage/emulated/0/Download/bank_changes.zip      # adjust the path to where the zip was saved
cp -r bank_changes/accounts bank_changes/transfers bank_changes/funding .
python bank_changes/apply_test_fixes.py                     # updates your four old test files
```

## Two things the copy command does NOT do for you
1. **Templates.** The four HTML files are in `accounts/templates/accounts/`. Your project may keep them elsewhere
   (run `find . -name verify.html`). Put `verify.html`, `address.html`, `address_staff.html` and `address_review.html`
   next to your existing `verify.html`, and delete the copies in `accounts/templates/` if that is a different folder.
2. **Models and URLs.** `accounts/models_append.py` is NOT a file to keep: its class goes into `accounts/models.py`
   (you have already done this). Add the four `address/...` routes to `accounts/urls.py`:
```python
from . import address_views
path("address/", address_views.address_view, name="address"),
path("address/staff/", address_views.address_queue, name="address_staff"),
path("address/staff/<int:pk>/", address_views.address_review, name="address_review"),
path("address/staff/<int:pk>/document/", address_views.address_document, name="address_document"),
```

## Then
```
python manage.py makemigrations && python manage.py migrate
python manage.py test accounts transfers funding
```
Give address reviewers the permission "Can review address proofs".

## Not done yet
`accounts/tier_views.py` (the upgrade page) still uses the old NIN/BVN rules. Send it and I will update it.
