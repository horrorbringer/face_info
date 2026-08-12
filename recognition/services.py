"""Recognition adapter. Raw images must not be written to disk or the database."""
import io
import os
import numpy as np
from django.conf import settings


class FaceRecognitionUnavailable(Exception):
    pass


def _app():
    if not settings.FACE_MODEL_PATH:
        raise FaceRecognitionUnavailable("Face scanning is not configured. Use manual lookup or set an approved FACE_MODEL_PATH.")
    if not os.path.exists(settings.FACE_MODEL_PATH):
        raise FaceRecognitionUnavailable("Configured face model was not found.")
    try:
        from insightface.app import FaceAnalysis
    except ImportError as exc:
        raise FaceRecognitionUnavailable("Install the approved recognition provider dependencies before enabling scans.") from exc
    # FACE_MODEL_PATH must point to the model-pack directory, e.g. /models/approved_pack.
    app = FaceAnalysis(name=os.path.basename(settings.FACE_MODEL_PATH), root=os.path.dirname(settings.FACE_MODEL_PATH), providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def embedding_from_upload(upload):
    """Return one normalized embedding. upload is read into RAM only."""
    try:
        import cv2
    except ImportError as exc:
        raise FaceRecognitionUnavailable("OpenCV is required for face scanning.") from exc
    image = cv2.imdecode(np.frombuffer(upload.read(), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FaceRecognitionUnavailable("The camera frame could not be read.")
    faces = _app().get(image)
    if len(faces) != 1:
        raise FaceRecognitionUnavailable("Show exactly one clear face to the camera.")
    vector = faces[0].normed_embedding.astype(float).tolist()
    return vector, "approved-onnx-model"


def best_match(vector):
    """Pilot-scale cosine search. Replace with pgvector HNSW query when throughput requires it."""
    from students.models import FaceEmbedding
    target = np.asarray(vector, dtype=np.float32)
    candidates = FaceEmbedding.objects.select_related("student").filter(student__is_active=True, student__consent_given_at__isnull=False)
    best, score = None, -1.0
    for candidate in candidates:
        stored = np.asarray(candidate.vector, dtype=np.float32)
        if stored.shape != target.shape:
            continue
        similarity = float(np.dot(target, stored) / (np.linalg.norm(target) * np.linalg.norm(stored)))
        if similarity > score:
            best, score = candidate.student, similarity
    return best, score
