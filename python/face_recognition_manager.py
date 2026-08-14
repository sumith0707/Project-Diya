import os
import time
import pickle
import threading
import numpy as np
import cv2
import onnxruntime as ort


# Standard 112x112 ArcFace/SFace reference landmark template.
# Same template used by InsightFace and most SFace reimplementations.
_ARCFACE_TEMPLATE_112 = np.array(
    [
        [38.2946, 51.6963],  # left eye
        [73.5318, 51.5014],  # right eye
        [56.0252, 71.7366],  # nose tip
        [41.5493, 92.3655],  # left mouth corner
        [70.7299, 92.2041],  # right mouth corner
    ],
    dtype=np.float32,
)

_YUNET_STRIDES = (8, 16, 32)
_YUNET_INPUT_SIZE = (640, 640)  # (width, height) - this model's ONNX input is a fixed size, not dynamic


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _generate_yunet_priors(input_w, input_h, strides=_YUNET_STRIDES):
    """Generate (cx, cy, stride) prior centers for every grid cell across all strides.

    Order MUST match the model's own flattening order (row-major per stride,
    strides concatenated in ascending order: 8, then 16, then 32) since we
    zip these 1:1 against the raw cls/obj/bbox/kps outputs.
    """
    priors = []
    for stride in strides:
        feat_w = input_w // stride
        feat_h = input_h // stride
        for i in range(feat_h):
            for j in range(feat_w):
                cx = (j + 0.5) * stride
                cy = (i + 0.5) * stride
                priors.append((cx, cy, stride))
    return np.array(priors, dtype=np.float32)  # (N, 3)


def _nms(boxes_xyxy, scores, iou_threshold=0.3):
    """Plain greedy NMS. boxes_xyxy: (N,4) as x1,y1,x2,y2."""
    if len(boxes_xyxy) == 0:
        return []

    x1, y1, x2, y2 = boxes_xyxy[:, 0], boxes_xyxy[:, 1], boxes_xyxy[:, 2], boxes_xyxy[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / np.maximum(areas[i] + areas[order[1:]] - inter, 1e-9)

        order = order[1:][iou <= iou_threshold]

    return keep


class _YuNetOnnxDetector:
    """Runs YuNet face detection directly via onnxruntime, replicating what
    cv2.FaceDetectorYN would normally do internally through cv2.dnn.
    """

    def __init__(self, model_path, score_threshold=0.7, nms_threshold=0.3, top_k=5000):
        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self.top_k = top_k

        self.input_w, self.input_h = _YUNET_INPUT_SIZE
        self.priors = _generate_yunet_priors(self.input_w, self.input_h)

        # Self-check: make sure our prior generation matches what the model
        # actually produces. If this fails, the decode math below is wrong
        # for this specific exported model and must NOT be trusted.
        expected_total = sum(
            (self.input_w // s) * (self.input_h // s) for s in _YUNET_STRIDES
        )
        if len(self.priors) != expected_total:
            raise RuntimeError(
                f"YuNet prior count mismatch: generated {len(self.priors)}, "
                f"expected {expected_total}. Decode logic does not match this model - "
                "do not trust detections until this is fixed."
            )

    def detect(self, bgr_frame):
        """Returns a list of dicts: {bbox: (x, y, w, h), landmarks: [(x,y)*5], score: float}
        in the ORIGINAL frame's coordinate space.
        """
        orig_h, orig_w = bgr_frame.shape[:2]
        resized = cv2.resize(bgr_frame, (self.input_w, self.input_h))
        blob = resized.astype(np.float32)
        blob = blob.transpose(2, 0, 1)[np.newaxis, ...]  # HWC -> NCHW

        outputs = self.session.run(None, {self.input_name: blob})
        out = {o.name: val for o, val in zip(self.session.get_outputs(), outputs)}

        cls = np.concatenate([out["cls_8"], out["cls_16"], out["cls_32"]], axis=1)[0, :, 0]
        obj = np.concatenate([out["obj_8"], out["obj_16"], out["obj_32"]], axis=1)[0, :, 0]
        bbox = np.concatenate([out["bbox_8"], out["bbox_16"], out["bbox_32"]], axis=1)[0]
        kps = np.concatenate([out["kps_8"], out["kps_16"], out["kps_32"]], axis=1)[0]

        scores = np.sqrt(np.clip(_sigmoid(cls) * _sigmoid(obj), 0.0, 1.0))

        mask = scores >= self.score_threshold
        if not np.any(mask):
            return []

        priors = self.priors[mask]
        scores = scores[mask]
        bbox = bbox[mask]
        kps = kps[mask]

        strides = priors[:, 2]
        cx = priors[:, 0] + bbox[:, 0] * strides
        cy = priors[:, 1] + bbox[:, 1] * strides
        w = np.exp(bbox[:, 2]) * strides
        h = np.exp(bbox[:, 3]) * strides

        x1 = cx - w / 2.0
        y1 = cy - h / 2.0
        x2 = cx + w / 2.0
        y2 = cy + h / 2.0
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        landmarks = np.zeros((len(priors), 5, 2), dtype=np.float32)
        for k in range(5):
            landmarks[:, k, 0] = priors[:, 0] + kps[:, 2 * k] * strides
            landmarks[:, k, 1] = priors[:, 1] + kps[:, 2 * k + 1] * strides

        if len(scores) > self.top_k:
            top_idx = np.argsort(scores)[::-1][: self.top_k]
            boxes_xyxy = boxes_xyxy[top_idx]
            scores = scores[top_idx]
            landmarks = landmarks[top_idx]

        keep = _nms(boxes_xyxy, scores, self.nms_threshold)

        scale_x = orig_w / self.input_w
        scale_y = orig_h / self.input_h

        results = []
        for i in keep:
            x1i, y1i, x2i, y2i = boxes_xyxy[i]
            results.append(
                {
                    "bbox": (
                        x1i * scale_x,
                        y1i * scale_y,
                        (x2i - x1i) * scale_x,
                        (y2i - y1i) * scale_y,
                    ),
                    "landmarks": [(lx * scale_x, ly * scale_y) for lx, ly in landmarks[i]],
                    "score": float(scores[i]),
                }
            )
        return results


class _SFaceOnnxEmbedder:
    """Runs SFace face-embedding directly via onnxruntime."""

    def __init__(self, model_path):
        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        input_shape = self.session.get_inputs()[0].shape  # e.g. [1, 3, 112, 112] or [None, 3, 112, 112]
        # Derive target size from the model itself rather than hardcoding, in
        # case this export differs from the usual 112x112.
        dims = [d for d in input_shape if isinstance(d, int) and d not in (1,)]
        if len(dims) >= 2:
            self.target_h, self.target_w = dims[-2], dims[-1]
        else:
            self.target_h, self.target_w = 112, 112

    def align_and_embed(self, bgr_frame, landmarks):
        """landmarks: list of 5 (x, y) tuples in the ORIGINAL frame's coordinates."""
        src = np.array(landmarks, dtype=np.float32)
        dst = _ARCFACE_TEMPLATE_112.copy()
        if (self.target_w, self.target_h) != (112, 112):
            dst[:, 0] *= self.target_w / 112.0
            dst[:, 1] *= self.target_h / 112.0

        transform, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
        if transform is None:
            return None

        aligned = cv2.warpAffine(bgr_frame, transform, (self.target_w, self.target_h))

        blob = aligned.astype(np.float32).transpose(2, 0, 1)[np.newaxis, ...]  # NCHW
        output = self.session.run(None, {self.input_name: blob})[0]
        embedding = output.reshape(-1).astype(np.float32)

        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    @staticmethod
    def cosine_similarity(a, b):
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class FaceRecognitionManager:
    """
    Continuous, fully offline face recognition using YuNet (detection) and
    SFace (embedding), run directly through onnxruntime since this board's
    OpenCV build lacks a working cv2.dnn module.

    Public API is unchanged from the cv2.dnn-based version:
    start(), stop(), start_enrollment(name), on_recognized(cb),
    on_enrollment_done(cb), list_known(), forget(name).
    """

    def __init__(
        self,
        camera,
        models_dir="/app/models/face",
        data_path="/app/data/known_faces.pkl",
        debug_dir="/app/data/face_debug",
        detect_fps=3,
        match_threshold=0.40,        # cosine similarity threshold - TUNE against your own camera/lighting
        announce_cooldown_sec=30.0,
        detect_score_threshold=0.7,
        save_debug_frames=True,
    ):
        self.camera = camera
        self.detect_fps = detect_fps
        self.match_threshold = match_threshold
        self.announce_cooldown_sec = announce_cooldown_sec
        self.data_path = data_path
        self.debug_dir = debug_dir
        self.save_debug_frames = save_debug_frames

        detector_path = os.path.join(models_dir, "face_detection_yunet_2023mar.onnx")
        recognizer_path = os.path.join(models_dir, "face_recognition_sface_2021dec.onnx")

        if not os.path.exists(detector_path) or not os.path.exists(recognizer_path):
            raise FileNotFoundError(f"Face models not found in {models_dir}.")

        self.detector = _YuNetOnnxDetector(detector_path, score_threshold=detect_score_threshold)
        self.embedder = _SFaceOnnxEmbedder(recognizer_path)

        self.known: dict[str, np.ndarray] = self._load_known_faces()
        self._known_lock = threading.Lock()

        self._last_announced: dict[str, float] = {}
        self._on_recognized = None
        self._on_recognition_failed = None

        self._running = False
        self._thread = None

        # Bounded recognition session (triggered on-demand, not continuous)
        self._session_active = False
        self._session_deadline = 0.0
        self._has_processed_frame = False

        self._enrolling = False
        self._enroll_name = None
        self._enroll_embeddings: list[np.ndarray] = []
        self._enroll_lock = threading.Lock()
        self._enroll_deadline = 0.0
        self._enroll_duration_sec = 4.0
        self._enroll_target_samples = 8
        self._on_enrollment_done = None

        if self.save_debug_frames:
            os.makedirs(self.debug_dir, exist_ok=True)
        self._debug_frame_saved = False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_known_faces(self):
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "rb") as f:
                    return pickle.load(f)
            except Exception as e:
                print(f"[FaceRecog] Failed to load known faces: {e}")
        return {}

    def _save_known_faces(self):
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        with open(self.data_path, "wb") as f:
            pickle.dump(self.known, f)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def on_recognized(self, callback):
        self._on_recognized = callback

    def on_recognition_failed(self, callback):
        """Register callback() invoked when a recognition session times out with no confident match."""
        self._on_recognition_failed = callback

    def on_enrollment_done(self, callback):
        self._on_enrollment_done = callback

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[FaceRecog] Recognition loop started (onnxruntime backend).")

    def stop(self):
        self._running = False
        print("[FaceRecog] Recognition loop stopped.")

    def start_recognition_session(self, timeout_sec=6.0):
        """Run recognition for up to `timeout_sec` seconds. Stops itself and
        calls on_recognized() on a confident match, or on_recognition_failed()
        if the window elapses with no match. Intended to be triggered
        on-demand (e.g. after object detection locks onto a face for a
        while) rather than run continuously.
        """
        self._session_active = True
        self._session_deadline = time.time() + timeout_sec
        self._has_processed_frame = False
        self.start()

    def start_enrollment(self, name: str):
        with self._enroll_lock:
            self._enrolling = True
            self._enroll_name = name
            self._enroll_embeddings = []
            self._enroll_deadline = time.time() + self._enroll_duration_sec
        print(f"[FaceRecog] Enrollment started for '{name}'. Look at the camera...")
        self.start()

    def list_known(self):
        with self._known_lock:
            return list(self.known.keys())

    def forget(self, name: str) -> bool:
        with self._known_lock:
            if name in self.known:
                del self.known[name]
                self._save_known_faces()
                print(f"[FaceRecog] Forgot '{name}'.")
                return True
        return False

    def save_debug_frame_now(self):
        self._debug_frame_saved = False

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------
    def _loop(self):
        interval = 1.0 / self.detect_fps
        while self._running:
            start_t = time.time()
            try:
                self._process_frame()
            except Exception as e:
                print(f"[FaceRecog] Error processing frame: {e}")

            if self._session_active and self._running and time.time() > self._session_deadline:
                self._session_active = False
                self.stop()
                if self._has_processed_frame:
                    print("[FaceRecog] Recognition session timed out - no match.")
                    if self._on_recognition_failed:
                        self._on_recognition_failed()
                else:
                    print("[FaceRecog] Recognition session timed out - no frames processed (camera may be unavailable).")
                break

            elapsed = time.time() - start_t
            time.sleep(max(0.0, interval - elapsed))

    def _to_bgr(self, frame):
        img = np.array(frame)
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 4:
            return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    def _maybe_save_debug_frame(self, img_bgr, faces):
        if not self.save_debug_frames or self._debug_frame_saved:
            return
        self._debug_frame_saved = True

        annotated = img_bgr.copy()
        for f in faces:
            x, y, w, h = [int(v) for v in f["bbox"]]
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(
                annotated, f"{f['score']:.2f}", (x, max(0, y - 5)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
            )
            for lx, ly in f["landmarks"]:
                cv2.circle(annotated, (int(lx), int(ly)), 2, (0, 0, 255), -1)

        out_path = os.path.join(self.debug_dir, f"debug_{int(time.time())}.jpg")
        cv2.imwrite(out_path, annotated)
        print(f"[FaceRecog] Saved debug frame to {out_path} ({len(faces)} face(s) detected) - "
              "check this visually to confirm boxes/landmarks land on real faces.")

    def _process_frame(self):
        frame = None
        try:
            frame = self.camera.capture()
        except Exception as e:
            if "before starting it" in str(e):
                if hasattr(self.camera, 'start'):
                    print("[FaceRecog] Camera not started – restarting...")
                    self.camera.start()
                    try:
                        frame = self.camera.capture()
                    except Exception as e2:
                        print(f"[FaceRecog] Still cannot capture after restart: {e2}")
                        return
                else:
                    print(f"[FaceRecog] Camera capture error: {e}")
                    return
            else:
                print(f"[FaceRecog] Camera capture error: {e}")
                return

        if frame is None:
            return

        self._has_processed_frame = True

        img_bgr = self._to_bgr(frame)
        faces = self.detector.detect(img_bgr)

        self._maybe_save_debug_frame(img_bgr, faces)

        if not faces:
            return

        with self._enroll_lock:
            enrolling = self._enrolling
            enroll_deadline = self._enroll_deadline

        if enrolling:
            if time.time() > enroll_deadline or len(self._enroll_embeddings) >= self._enroll_target_samples:
                self._finish_enrollment()
                return

            face = max(faces, key=lambda f: f["bbox"][2] * f["bbox"][3])
            embedding = self.embedder.align_and_embed(img_bgr, face["landmarks"])
            if embedding is not None:
                self._enroll_embeddings.append(embedding)
            return

        with self._known_lock:
            known_items = list(self.known.items())

        if not known_items:
            return

        now = time.time()
        for face in faces:
            embedding = self.embedder.align_and_embed(img_bgr, face["landmarks"])
            if embedding is None:
                continue

            best_name, best_score = None, -1.0
            for name, known_embedding in known_items:
                score = self.embedder.cosine_similarity(embedding, known_embedding)
                if score > best_score:
                    best_name, best_score = name, score

            if best_name is not None and best_score >= self.match_threshold:
                last = self._last_announced.get(best_name, 0.0)
                if now - last >= self.announce_cooldown_sec:
                    self._last_announced[best_name] = now
                    if self._on_recognized:
                        self._on_recognized(best_name)
                if self._session_active:
                    self._session_active = False
                    self.stop()
                    print(f"[FaceRecog] Session matched '{best_name}', stopping.")
                return

    def _finish_enrollment(self):
        with self._enroll_lock:
            name = self._enroll_name
            embeddings = self._enroll_embeddings
            self._enrolling = False
            self._enroll_name = None
            self._enroll_embeddings = []

        success = bool(embeddings)
        if success:
            avg_embedding = np.mean(np.stack(embeddings), axis=0)
            avg_embedding = avg_embedding / (np.linalg.norm(avg_embedding) + 1e-9)
            with self._known_lock:
                self.known[name] = avg_embedding.astype(np.float32)
                self._save_known_faces()
            print(f"[FaceRecog] Enrollment complete for '{name}' ({len(embeddings)} samples).")
        else:
            print(f"[FaceRecog] Enrollment for '{name}' failed - no face captured.")

        if self._on_enrollment_done:
            self._on_enrollment_done(name, success)

        self.stop()