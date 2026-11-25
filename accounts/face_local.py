import logging
import os

from django.conf import settings

from .face import storage

log = logging.getLogger("kyc.face")
FRAMES_MATCH, ID_MATCH_HINT = 0.30, 0.363      # cosine similarity; 0.363 is SFace's "same person" threshold
_models = {}


def _load(cv2):
    if not _models:
        d = settings.FACE_MODEL_DIR
        _models["det"] = cv2.FaceDetectorYN.create(os.path.join(d, "yunet.onnx"), "", (320, 320), 0.8, 0.3, 5000)
        _models["rec"] = cv2.FaceRecognizerSF.create(os.path.join(d, "sface.onnx"), "")
    return _models["det"], _models["rec"]


def _faces(cv2, np, data):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None, []
    h, w = img.shape[:2]
    k = min(1.0, 1600 / max(h, w))
    if k < 1:
        img = cv2.resize(img, (int(w * k), int(h * k)))
        h, w = img.shape[:2]
    det, _ = _load(cv2)
    det.setInputSize((w, h))
    _, faces = det.detect(img)
    return img, ([] if faces is None else list(faces))


def _feature(cv2, img, face):
    return _load(cv2)[1].feature(_load(cv2)[1].alignCrop(img, face))


def _match(cv2, a, b):
    return float(_load(cv2)[1].match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


class LocalFace:
    """Returns (status, score, ref, reason). It never returns 'passed': a person decides."""

    def assess(self, check):
        try:
            import cv2
            import numpy as np
        except ImportError:
            return "review", None, "no-opencv", ""
        try:
            return self._assess(check, cv2, np)
        except Exception:
            log.exception("local face check failed for %s", check.pk)
            return "review", None, "error", ""             # fail safe: a person looks at it

    def _assess(self, check, cv2, np):
        feats, imgs = [], []
        for frame in check.frames.order_by("step"):
            with open(storage.path(frame.path), "rb") as f:
                img, faces = _faces(cv2, np, f.read())
            if len(faces) != 1:
                why = "We could not see your face clearly." if not faces else "More than one face was in the photo."
                return "failed", 0.0, "faces-%d" % len(faces), why + " Try again in good light, alone."
            imgs.append(img)
            feats.append(_feature(cv2, img, faces[0]))
        lowest = min(_match(cv2, feats[i], feats[j]) for i in range(len(feats)) for j in range(i + 1, len(feats)))
        if lowest < FRAMES_MATCH:
            return "failed", round(lowest, 3), "frames-differ", "The photos did not look like one person. Try again."
        score, ref = round(lowest, 3), "frames-ok"
        from .models import KYCApplication
        app = (KYCApplication.objects.filter(user=check.user).exclude(identity_document="")
               .order_by("-submitted_at").first())
        if app and app.identity_document.name.lower().endswith((".jpg", ".jpeg", ".png")):
            with open(app.identity_document.path, "rb") as f:
                img, faces = _faces(cv2, np, f.read())
            if faces:
                big = max(faces, key=lambda r: r[2] * r[3])
                idf = _feature(cv2, img, big)
                score, ref = round(max(_match(cv2, idf, x) for x in feats), 3), "id-compared"
        return "review", score, ref, ""