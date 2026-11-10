"""Face enrollment and matching for CampusVote.

The browser sends camera snapshots (data URLs). This module turns each snapshot
into a face "embedding" (a list of numbers describing the face) with DeepFace,
and later compares a live snapshot against the embeddings saved at enrollment.

Only embeddings are stored - no face photo is kept.

Settings (all optional, put in .env):
    FACE_MODEL=SFace          recognition model (small, runs on onnxruntime)
    FACE_DETECTOR=yunet       face detector
    FACE_THRESHOLD=0.593      max cosine distance that still counts as "same person"
    FACE_ANTI_SPOOF=0         1 = also reject printed photos / phone screens
                              (downloads one more model; a little slower)
"""
import base64
import binascii
import io
import json
import os

import numpy as np
from PIL import Image, ImageOps

MODEL_NAME = os.getenv("FACE_MODEL", "SFace").strip()
DETECTOR = os.getenv("FACE_DETECTOR", "yunet").strip()
ANTI_SPOOF = os.getenv("FACE_ANTI_SPOOF", "0").strip() == "1"

MAX_FRAME_BYTES = 3 * 1024 * 1024
MAX_SIDE = 640            # frames are shrunk to this before analysis (faster)
MIN_FACE_WIDTH = 110      # px on the shrunk frame; smaller = "move closer"
MIN_FACE_CONFIDENCE = 0.85
ENROLL_FRAMES = 3


class FaceError(Exception):
    """A problem the student can fix (no face, too dark, not the same person...)."""


class FaceUnavailable(RuntimeError):
    """The face engine itself is not installed / could not load."""


# ----------------------------------------------------------------- images
def decode_frame(data_url):
    """'data:image/jpeg;base64,...' -> BGR numpy array (what DeepFace expects)."""
    if not isinstance(data_url, str) or "," not in data_url:
        raise FaceError("The camera photo was not received. Please try again.")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=False)
    except (binascii.Error, ValueError):
        raise FaceError("The camera photo could not be read. Please try again.")
    if not raw or len(raw) > MAX_FRAME_BYTES:
        raise FaceError("The camera photo is empty or too large. Please try again.")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise FaceError("Unsupported camera photo format.")
        image = ImageOps.exif_transpose(image).convert("RGB")
    except FaceError:
        raise
    except Exception:
        raise FaceError("The camera photo could not be read. Please try again.")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return np.asarray(image)[:, :, ::-1].copy()  # RGB -> BGR


# -------------------------------------------------------------- embeddings
def _deepface():
    try:
        from deepface import DeepFace
    except Exception as exc:  # ImportError, or a broken install
        raise FaceUnavailable(
            "Face recognition is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return DeepFace


def embed(frame):
    """Return the embedding (list of floats) of the one face in `frame`."""
    DeepFace = _deepface()
    try:
        results = DeepFace.represent(
            img_path=frame,
            model_name=MODEL_NAME,
            detector_backend=DETECTOR,
            enforce_detection=True,
            align=True,
            anti_spoofing=ANTI_SPOOF,
        )
    except ValueError as exc:
        message = str(exc).lower()
        if "spoof" in message:
            raise FaceError(
                "Please use your live camera - a photo or screen cannot be used."
            ) from exc
        raise FaceError(
            "No face found. Move into good light and keep your face inside the oval."
        ) from exc
    except (ImportError, ModuleNotFoundError) as exc:
        raise FaceUnavailable(
            "Face recognition is not installed. Run: pip install -r requirements.txt"
        ) from exc
    except Exception as exc:  # model download failed, onnx error, ...
        raise FaceUnavailable(
            "The face engine could not start. Check your internet connection "
            "(the model downloads once) and the terminal for details."
        ) from exc

    if isinstance(results, dict):
        results = [results]
    if not results:
        raise FaceError("No face found. Keep your face inside the oval.")
    if len(results) > 1:
        raise FaceError("More than one face is visible. Only you should be in the camera.")

    face = results[0]
    area = face.get("facial_area") or {}
    confidence = face.get("face_confidence")
    if confidence is not None and confidence < MIN_FACE_CONFIDENCE:
        raise FaceError("Your face is not clear. Face the camera in better light.")
    if area.get("w", MIN_FACE_WIDTH) < MIN_FACE_WIDTH:
        raise FaceError("Move a little closer to the camera.")
    return [float(x) for x in face["embedding"]]


# --------------------------------------------------------------- distance
def threshold():
    raw = os.getenv("FACE_THRESHOLD", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    try:
        from deepface.modules.verification import find_threshold
        return float(find_threshold(MODEL_NAME, "cosine"))
    except Exception:
        return 0.593  # DeepFace's tuned value for SFace + cosine


def cosine_distance(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / denom)


# ------------------------------------------------------------ enrollment
def build_enrollment(data_urls):
    """Check the 3 snapshots, make sure they are the same person, return bytes to store."""
    if not isinstance(data_urls, list) or len(data_urls) != ENROLL_FRAMES:
        raise FaceError("Capture all three poses (Center, Left, Right) first.")

    vectors = [embed(decode_frame(url)) for url in data_urls]

    limit = threshold()
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            if cosine_distance(vectors[i], vectors[j]) > limit:
                raise FaceError(
                    "The three photos do not look like the same person. "
                    "Press Retake and keep only your face in the camera."
                )
    return pack(vectors)


def pack(vectors):
    return json.dumps({"model": MODEL_NAME, "vectors": vectors}).encode("utf-8")


def unpack(blob):
    try:
        data = json.loads(bytes(blob).decode("utf-8"))
        return data["model"], data["vectors"]
    except Exception:
        return None, []


# ----------------------------------------------------------- verification
def verify_live(stored_blob, data_url):
    """Compare a live snapshot with the stored face. Returns (matched, distance)."""
    model, vectors = unpack(stored_blob)
    if not vectors or model != MODEL_NAME:
        raise FaceError(
            "Your saved face data is out of date. Please set up face verification again."
        )
    live = embed(decode_frame(data_url))
    distances = sorted(cosine_distance(live, vector) for vector in vectors)
    majority_distance = distances[len(distances) // 2]
    return majority_distance <= threshold(), majority_distance
