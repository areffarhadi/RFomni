"""Image and video heads on one shared, frozen mix87 vision tower.

loc v2 (11 body classes), pose v2 (COCO-17), face spoof + deepfake v2 (8-checkpoint
ensemble on loc v2 face crops; no ultralytics face detector needed), head pose, and the
video spoof + deepfake head. Boxes are returned as normalised [x1, y1, x2, y2] in 0..1.
"""
import numpy as np
import torch
from PIL import Image

from . import compat

VOCAB_FALLBACK = ["person", "head", "face", "left hand", "right hand", "left arm", "right arm",
                  "left leg", "right leg", "left foot", "right foot"]


def _r(x):
    return round(float(x), 4)


def _norm_box(b, w, h):
    return [_r(b[0] / w), _r(b[1] / h), _r(b[2] / w), _r(b[3] / h)]


class VisionHeads:
    def __init__(self, encoder_path: str, device: torch.device):
        compat.stub_pycocotools()
        compat.patch_vision_encoder()
        import torch.nn.functional as F
        import omni_vision_encoder as ove
        import infer_spoof_face as isf
        from omni_grounding.omni_human import OmniHumanSidecar
        from omni_grounding.omni_human_v2 import OmniHumanSidecarV2
        from omni_grounding.headpose import HeadposeExpert
        from video_fas_sidecar import VideoFASSidecar
        try:
            from omni_grounding.data.loc_v2 import VOCAB
        except Exception:
            VOCAB = VOCAB_FALLBACK

        repo = compat.REPO
        self.dev = device
        self.vocab = list(VOCAB)
        self.enc = ove.FrozenOmniVisionEncoder(encoder_path, dtype=torch.bfloat16).eval().to(device)
        self.side = OmniHumanSidecar(device=device, encoder=self.enc)  # loc v2 + pose v2

        # multi-class loc v2 detection: OmniHumanSidecarV2.detect bound to the shared tower
        v2 = object.__new__(OmniHumanSidecarV2)
        v2.device, v2.amp, v2.encoder, v2.loc, v2.loc_size = device, True, self.enc, self.side.loc, self.side.loc_size
        cache = torch.load(repo / "checkpoints/text/qwen_thinker_text_cache_body_caption.pt", map_location="cpu",
                           weights_only=False)
        v2.text = F.normalize(cache["eval_text"].float(), dim=-1).to(device)
        self.v2 = v2

        # spoof v2 builds its own tower unless handed ours
        orig = isf.FrozenOmniVisionEncoder
        isf.FrozenOmniVisionEncoder = lambda *a, **k: self.enc
        try:
            self.spoof = isf.FaceAntispoofInference(str(repo / "checkpoints/spoof_v2"), encoder_path=encoder_path,
                                                    device=device)
        finally:
            isf.FrozenOmniVisionEncoder = orig
        self.video_fas = VideoFASSidecar(device=device, encoder=self.enc)
        self.headpose = HeadposeExpert(self.side, oof_cache=None)

    # ------------------------------------------------------------------ images
    @torch.no_grad()
    def detect(self, img: Image.Image, classes=None, score_thresh: float = 0.3) -> list:
        classes = [c for c in (classes or self.vocab) if c in self.vocab] or self.vocab
        w, h = img.size
        return [{"label": d["label"], "score": _r(d["score"]), "box": _norm_box(d["box_xyxy_px"], w, h)}
                for d in self.v2.detect(img, classes, score_thresh=score_thresh)]

    @torch.no_grad()
    def pose(self, img: Image.Image, score_thresh: float = 0.3, kpt_thresh: float = 0.2, max_people: int = 10) -> list:
        w, h = img.size
        people = []
        for p in self.side.detect_people(img, score_thresh=score_thresh, max_people=max_people):
            rec = self.side.pose_person(img, p["box_xyxy_px"], conf_thresh=kpt_thresh)
            kps = rec.get("keypoints") if isinstance(rec, dict) else rec
            pts = [[_r(k["x"] / w), _r(k["y"] / h), _r(k["conf"])] for k in kps]
            people.append({"score": _r(p["score"]), "box": _norm_box(p["box_xyxy_px"], w, h), "keypoints": pts,
                           "visible": [bool(k["conf"] >= kpt_thresh) for k in kps]})
        return people

    @torch.no_grad()
    def face_spoof(self, img: Image.Image) -> dict:
        """Per-face log-odds (higher = fake) on loc v2 face crops, as in the reference recipe:
        faces with score >= 0.5 and at least a quarter of the largest face's area."""
        from face_crop import square_crop
        w, h = img.size
        faces = self.v2.detect(img, ["face"], score_thresh=0.3)
        keep = [d for d in faces if d["score"] >= 0.5]
        if keep:
            area = lambda b: (b[2] - b[0]) * (b[3] - b[1])  # noqa: E731
            amax = max(area(d["box_xyxy_px"]) for d in keep)
            keep = [d for d in keep if area(d["box_xyxy_px"]) >= 0.25 * amax]
        keep = keep or faces[:1]
        side = self.spoof.max_image_side
        self.enc.set_image_pixel_budget(min(256 * 256, side * side), side * side)  # other heads change it
        crops = [square_crop(img, d["box_xyxy_px"], self.spoof.crop_scale, side) for d in keep] or \
                [img.resize((side, side), Image.Resampling.BICUBIC)]
        scores = self.spoof.score_images(crops)
        th = self.spoof.task_thresholds
        out = [{"box": _norm_box(d["box_xyxy_px"], w, h), "face_score": _r(d["score"]), "score": _r(s),
                "presentation_attack": bool(s > th["fas"]), "deepfake": bool(s > th["deepfakes"])}
               for d, s in zip(keep, scores)]
        top = float(np.max(scores)) if len(scores) else -99.0
        return {"faces": out, "found_face": bool(keep), "score": _r(top),
                "presentation_attack": bool(top > th["fas"]), "deepfake": bool(top > th["deepfakes"]),
                "thresholds": {k: _r(v) for k, v in th.items()}}

    @torch.no_grad()
    def head_pose(self, img: Image.Image):
        r = self.headpose.angles(img)
        return {k: (_r(v) if isinstance(v, (int, float)) else v) for k, v in r.items()} if r else None

    def analyze_image(self, img: Image.Image, heads: list, options=None) -> dict:
        o = options or {}
        img = img.convert("RGB")
        out = {"width": img.size[0], "height": img.size[1]}
        if "boxes" in heads:
            out["boxes"] = self.detect(img, o.get("classes"), float(o.get("score_thresh", 0.3)))
        if "pose" in heads:
            out["pose"] = self.pose(img, float(o.get("score_thresh", 0.3)))
        if "face_spoof" in heads:
            out["face_spoof"] = self.face_spoof(img)
        if "head_pose" in heads:
            out["head_pose"] = self.head_pose(img)
        return out

    # ------------------------------------------------------------------ video
    @torch.no_grad()
    def analyze_video(self, path: str, heads: list, options=None) -> dict:
        o = options or {}
        out = {}
        if "video_spoof" in heads:
            r = self.video_fas.score_video(path, fps=float(o.get("fps", 2.0)), max_frames=int(o.get("max_frames", 256)))
            th = {k: v.get("eer") for k, v in self.video_fas.thresholds.items()}
            out["video_spoof"] = {
                "video": {k: _r(v) for k, v in r["video"].items()}, "decision": r["decision"],
                "thresholds": {k: _r(v) for k, v in th.items() if v is not None},
                "frames": [{"t": _r(f["t"]), "face": f["box"] is not None,
                            **{k: _r(f[k]) for k in ("spoof", "deepfake", "joint")}} for f in r["frames"]],
            }
        return out
