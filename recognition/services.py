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
    # Pass path directly if isdir so FaceAnalysis finds onnx files directly without prepending /models/
    model_name = settings.FACE_MODEL_PATH if os.path.isdir(settings.FACE_MODEL_PATH) else os.path.basename(settings.FACE_MODEL_PATH)
    root_dir = os.path.dirname(settings.FACE_MODEL_PATH)
    app = FaceAnalysis(name=model_name, root=root_dir, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_thresh=0.4, det_size=(640, 640))
    return app


def embedding_from_upload(upload):
    """Return one normalized embedding. upload is read into RAM only."""
    try:
        import cv2
    except ImportError as exc:
        raise FaceRecognitionUnavailable("OpenCV is required for face scanning.") from exc

    if hasattr(upload, "seek"):
        upload.seek(0)
    raw = upload.read()
    if hasattr(upload, "seek"):
        upload.seek(0)

    if not raw:
        raise FaceRecognitionUnavailable("The uploaded frame is empty.")

    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FaceRecognitionUnavailable("The camera frame could not be read.")

    faces = _app().get(image)
    if not faces:
        raise FaceRecognitionUnavailable("No face detected. Please ensure good lighting, face the camera, and remove dark glasses or face coverings.")

    # If multiple faces are detected, pick the prominent foreground face if it is clearly dominant
    if len(faces) > 1:
        # Calculate bounding box area for each detected face: (x2 - x1) * (y2 - y1)
        faces.sort(key=lambda f: float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])), reverse=True)
        primary_area = float((faces[0].bbox[2] - faces[0].bbox[0]) * (faces[0].bbox[3] - faces[0].bbox[1]))
        second_area = float((faces[1].bbox[2] - faces[1].bbox[0]) * (faces[1].bbox[3] - faces[1].bbox[1]))

        # If the second face is also significant (> 45% of the primary face), require a single subject
        if second_area > 0.45 * primary_area:
            raise FaceRecognitionUnavailable(f"Multiple faces detected ({len(faces)} people in frame). Ensure only the student is visible.")

    primary_face = faces[0]
    vector = primary_face.normed_embedding.astype(float).tolist()
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
