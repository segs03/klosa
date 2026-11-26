from django.conf import settings
from django.db import models

LABELS = {"smile": "Smile", "blink": "Close your eyes",
          "left": "Slowly turn your head left", "right": "Slowly turn your head right",
          "up": "Look up", "open": "Open your mouth"}


class FaceEnrollment(models.Model):
    """The customer's face template, encrypted. Never an image."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="face_enrollment")
    template_enc = models.BinaryField()
    use_at_login = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class FaceSession(models.Model):
    PURPOSES = [("signup", "Signup"), ("login", "Login"), ("ip_change", "New network"),
                ("password_reset", "Password reset"), ("phone_change", "Phone change"),
                ("recovery", "Account recovery"), ("upgrade", "Tier upgrade")]
    STATUS = [("capturing", "Capturing"), ("review", "In review"), ("passed", "Passed"),
              ("failed", "Failed"), ("expired", "Expired")]
    LABELS = LABELS

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="face_sessions")
    purpose = models.CharField(max_length=15, choices=PURPOSES)
    bound_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="capturing")
    decided_by = models.CharField(max_length=6, blank=True)
    prompts = models.JSONField(default=list)
    nonce_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    score = models.FloatField(null=True, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose", "status"])]
        permissions = [("review_facesession", "Can review face sessions")]

    def prompt_labels(self):
        return [self.LABELS.get(p, p) for p in self.prompts]


class FaceFrame(models.Model):
    session = models.ForeignKey(FaceSession, on_delete=models.PROTECT, related_name="frames")
    step = models.PositiveSmallIntegerField()
    path = models.CharField(max_length=255, blank=True)
    sha256 = models.CharField(max_length=64, unique=True)
    taken_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step"]
        constraints = [models.UniqueConstraint(fields=["session", "step"], name="one_face_frame_per_step")]


class FaceConsent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="face_consents")
    version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "version"], name="one_face_consent_per_version")]


class FaceDuplicate(models.Model):
    """Two accounts whose faces look like the same person. Staff decide."""
    STATUS = [("open", "Open"), ("cleared", "Cleared: different people"), ("confirmed", "Confirmed: same person")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="face_duplicates")
    other_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    score = models.FloatField()
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("review_faceduplicate", "Can review duplicate faces")]
        constraints = [models.UniqueConstraint(fields=["user", "other_user"], name="one_duplicate_flag_per_pair")]