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


def check_liveness(image, face):
    """
    Passive liveness / anti-spoofing detection:
    Determines whether the presented face is a real live human or a spoof
    (e.g., printed paper photo, smartphone/tablet screen display).

    Evaluates:
    1. 3D Facial Depth Variance: Evaluates the (x, y, z) coordinates from
       landmark_3d_68. Real human heads exhibit significant relative depth between
       the nose tip, eyes, and jawline, while flat screens/paper show compressed depth.
    2. Moiré / Screen Grid Frequency Analysis: Applies 2D FFT to identify
       high-frequency periodic harmonic spikes created by digital LCD/OLED display matrices.
    3. Texture & Sharpness: Analyzes Laplacian variance for paper/screen recapture blur.
    4. Chrominance Distribution: Verifies natural skin tones in YCrCb color space.

    Returns: (is_real: bool, score: float, reason: str)
    """
    import cv2
    score = 1.0
    reasons = []

    # 1. 3D Landmark Depth Variance & Pose / Eye metrics
    depth_ratio = 0.0
    ear = 0.30
    yaw_ratio = 1.0

    lm3d = face.get("landmark_3d_68")
    if lm3d is not None and len(lm3d) == 68:
        pts = np.asarray(lm3d, dtype=np.float32)
        face_width = max(1.0, float(np.ptp(pts[:, 0])))
        depth_span = float(np.ptp(pts[:, 2]))
        depth_ratio = depth_span / face_width

        # Planar 2D photos held up to camera lack natural 3D depth curvature
        if depth_ratio < 0.055:
            score -= 0.40
            reasons.append("Planar 2D surface detected (flat photo/screen)")

        # Compute Eye Aspect Ratio (EAR) for blink detection
        try:
            # Right eye: 36-41
            r_v1 = np.linalg.norm(pts[37, :2] - pts[41, :2])
            r_v2 = np.linalg.norm(pts[38, :2] - pts[40, :2])
            r_h = np.linalg.norm(pts[36, :2] - pts[39, :2])
            r_ear = (r_v1 + r_v2) / max(1e-5, (2.0 * r_h))

            # Left eye: 42-47
            l_v1 = np.linalg.norm(pts[43, :2] - pts[47, :2])
            l_v2 = np.linalg.norm(pts[44, :2] - pts[46, :2])
            l_h = np.linalg.norm(pts[42, :2] - pts[45, :2])
            l_ear = (l_v1 + l_v2) / max(1e-5, (2.0 * l_h))

            ear = round(float((r_ear + l_ear) / 2.0), 3)
        except Exception:
            ear = 0.28

        # Compute Head Yaw Ratio (Nose horizontal position relative to jaw contour 0 & 16)
        try:
            nose_x = pts[30, 0]
            d_left = max(1.0, float(nose_x - pts[0, 0]))
            d_right = max(1.0, float(pts[16, 0] - nose_x))
            yaw_ratio = round(float(d_left / d_right), 3)
        except Exception:
            yaw_ratio = 1.0

    # 2. Extract and analyze face crop
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
    crop = image[y1:y2, x1:x2]

    if crop.shape[0] > 40 and crop.shape[1] > 40:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # 2a. Blur / low texture variance (typical of paper printouts or out-of-focus recaptures)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if lap_var < 20.0:
            score -= 0.35
            reasons.append("Unnatural low texture variance (printed photo or blur)")

        # 2b. Screen Moiré / Digital Sub-Pixel Grid (2D Fast Fourier Transform)
        resized = cv2.resize(gray, (128, 128))
        dft = np.fft.fft2(resized)
        dft_shift = np.fft.fftshift(dft)
        mag = np.abs(dft_shift)
        h, w = mag.shape
        cy, cx = h // 2, w // 2
        low_band = mag[cy - 12:cy + 12, cx - 12:cx + 12].sum()
        total_power = mag.sum()
        high_freq_ratio = (total_power - low_band) / max(1.0, total_power)
        if high_freq_ratio > 0.89:
            score -= 0.35
            reasons.append("Screen grid/moiré pattern detected (display screen)")

        # 2c. Skin color gamut check in YCrCb
        ycrcb = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
        cr = ycrcb[:, :, 1]
        cb = ycrcb[:, :, 2]
        skin_mask = (cr >= 128) & (cr <= 182) & (cb >= 68) & (cb <= 138)
        skin_ratio = float(skin_mask.sum()) / float(crop.shape[0] * crop.shape[1])
        if skin_ratio < 0.12:
            score -= 0.30
            reasons.append("Artificial color spectrum (display backlight)")

    score = max(0.0, min(1.0, score))
    is_real = score >= 0.50
    details = ", ".join(reasons) if reasons else "Real 3D face verified"
    metrics = {
        "depth_ratio": round(float(depth_ratio), 4),
        "ear": ear,
        "yaw_ratio": yaw_ratio,
        "is_blinking": ear < 0.20,
    }
    return is_real, round(score, 3), details, metrics


def process_kiosk_frame(upload):
    """
    Process a live kiosk frame: decodes image, detects face, and verifies liveness.
    Returns: (vector, model_name, liveness_dict)
    Raises: FaceRecognitionUnavailable on error or spoof detection.
    """
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
        raise FaceRecognitionUnavailable("The camera frame is empty.")

    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FaceRecognitionUnavailable("The camera frame could not be decoded.")

    faces = _app().get(image)
    if not faces:
        raise FaceRecognitionUnavailable("No face detected. Please face the camera with adequate lighting.")

    if len(faces) > 1:
        faces.sort(key=lambda f: float((f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])), reverse=True)
        primary_area = float((faces[0].bbox[2] - faces[0].bbox[0]) * (faces[0].bbox[3] - faces[0].bbox[1]))
        second_area = float((faces[1].bbox[2] - faces[1].bbox[0]) * (faces[1].bbox[3] - faces[1].bbox[1]))
        if second_area > 0.45 * primary_area:
            raise FaceRecognitionUnavailable(f"Multiple faces detected ({len(faces)} people). Ensure only one person is in frame.")

    primary_face = faces[0]

    # Verify real vs fake (anti-spoofing)
    is_real, liveness_score, reason, metrics = check_liveness(image, primary_face)
    liveness_info = {
        "is_real": is_real,
        "score": liveness_score,
        "details": reason,
        "metrics": metrics,
    }

    if not is_real:
        raise FaceRecognitionUnavailable(f"Spoof detected: {reason}. Please present a real live face.")

    vector = primary_face.normed_embedding.astype(float).tolist()
    return vector, "approved-onnx-model", liveness_info


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
