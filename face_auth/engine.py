import logging
import os

from django.conf import settings

log = logging.getLogger("face_auth")
MODELS = ("yunet.onnx", "sface.onnx", "face_landmarker.task")
FLIP = False        # set True if left/right come out reversed on a real phone
RULES = {           # starting values: tune on real phones
    "smile": lambda p: p["smile"] > 0.5,
    "blink": lambda p: p["blink"] > 0.5,
    "open":  lambda p: p["open"] > 0.4,
    "left":  lambda p: (-p["yaw"] if FLIP else p["yaw"]) > 0.12,
    "right": lambda p: (p["yaw"] if FLIP else -p["yaw"]) > 0.12,
    "up":    lambda p: p["pitch"] < 0.38,
}
_m = {}


def available():
    try:
        import cv2, mediapipe, numpy  # noqa: F401
    except ImportError:
        return False
    return all(os.path.isfile(os.path.join(settings.FACE_MODEL_DIR, n)) for n in MODELS)


def _load():
    if not _m:
        import cv2
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision
        d = settings.FACE_MODEL_DIR
        _m["det"] = cv2.FaceDetectorYN.create(os.path.join(d, "yunet.onnx"), "", (320, 320), 0.8, 0.3, 5000)
        _m["rec"] = cv2.FaceRecognizerSF.create(os.path.join(d, "sface.onnx"), "")
        _m["lm"] = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=os.path.join(d, "face_landmarker.task")),
            output_face_blendshapes=True, num_faces=1))
    return _m


def _unit(np, v):
    v = np.asarray(v, np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def similarity(a, b):
    import numpy as np
    return float(np.dot(_unit(np, np.frombuffer(a, np.float32)), _unit(np, np.frombuffer(b, np.float32))))


def _pose(img):
    import cv2
    import mediapipe as mp
    r = _load()["lm"].detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
    if not r.face_landmarks:
        return None
    lm, bs = r.face_landmarks[0], {b.category_name: b.score for b in r.face_blendshapes[0]}
    eye_y = (lm[33].y + lm[263].y) / 2
    return {"blink": (bs["eyeBlinkLeft"] + bs["eyeBlinkRight"]) / 2,
            "smile": (bs["mouthSmileLeft"] + bs["mouthSmileRight"]) / 2,
            "open": bs["jawOpen"],
            "yaw": (lm[1].x - lm[33].x) / max(lm[263].x - lm[33].x, 1e-6) - 0.5,
            "pitch": (lm[1].y - eye_y) / max(lm[152].y - eye_y, 1e-6)}


def analyse(blobs, prompts):
    """-> (template_bytes | None, lowest similarity between frames, code).
    code: ok, unreadable, no_face, many_faces, prompt."""
    import cv2
    import numpy as np
    m = _load()
    if len(blobs) != len(prompts):
        return None, 0.0, "unreadable"
    feats = []
    for data, prompt in zip(blobs, prompts):
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None, 0.0, "unreadable"
        h, w = img.shape[:2]
        k = min(1.0, 1280 / max(h, w))
        if k < 1:
            img = cv2.resize(img, (int(w * k), int(h * k)))
            h, w = img.shape[:2]
        m["det"].setInputSize((w, h))
        _, faces = m["det"].detect(img)
        n = 0 if faces is None else len(faces)
        if n != 1:
            return None, 0.0, "no_face" if n == 0 else "many_faces"
        pose = _pose(img)
        if pose is None or not RULES[prompt](pose):
            return None, 0.0, "prompt"
        feats.append(_unit(np, m["rec"].feature(m["rec"].alignCrop(img, faces[0]))))
    low = min((float(np.dot(a, b)) for i, a in enumerate(feats) for b in feats[i + 1:]), default=1.0)
    return _unit(np, np.mean(feats, axis=0)).tobytes(), low, "ok"