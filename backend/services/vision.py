"""视觉识别服务（需求书 5.5「视觉识别」的实现）。

能力与边界（与项目「不返回伪结果」原则一致）：
- analyze_frame：对上传帧做图像质量检查 + 水面漂浮物启发式检测（OpenCV）。
  算法版本 cv-float-v1：亮残差阈值 + 连通域过滤，输出候选框与置信度。
  识别结果只作为「待确认事件」，人工确认前不计入已确认数量。
- ocr_text：称重单/饲料袋等图片的文字识别。依赖本机安装 Tesseract
  （pip install pytesseract 并安装 tesseract-ocr 可执行程序）；未安装时
  明确报错提示依赖，不做假识别。

检测目标是启发式规则而非深度模型：对反光、水草、增氧机水花可能误报，
界面与事件上均已标注需人工确认。
"""
import os
import shutil

MODEL_VERSION = "cv-float-v1"

try:  # OpenCV 为可选依赖：未安装时质量检查与识别给出明确提示
    import cv2
    import numpy as np
    HAS_CV = True
except ImportError:  # pragma: no cover
    cv2 = np = None
    HAS_CV = False

FRAME_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "frames")


def save_frame(pond_id, position, data_bytes, ext="jpg"):
    """保存上传帧到 static/frames/pond_<id>/，返回相对 image_path。"""
    d = os.path.join(FRAME_ROOT, f"pond_{pond_id}")
    os.makedirs(d, exist_ok=True)
    from datetime import datetime
    name = f"{position}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.{ext}"
    path = os.path.join(d, name)
    with open(path, "wb") as fh:
        fh.write(data_bytes)
    return f"static/frames/pond_{pond_id}/{name}"


def _imread(path):
    data = np.fromfile(path, dtype=np.uint8)  # 兼容中文路径
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def analyze_frame(path):
    """帧分析：质量检查 + 漂浮物检测。返回 dict，可解释字段全部给出。"""
    if not HAS_CV:
        return {"ok": False, "error": "依赖未安装：需安装 opencv-python 后才能做帧分析",
                "model_version": MODEL_VERSION}
    img = _imread(path)
    if img is None:
        return {"ok": False, "error": "图片解码失败（请上传 jpg/png）",
                "model_version": MODEL_VERSION}

    h, w = img.shape[:2]
    if max(h, w) > 640:
        f = 640.0 / max(h, w)
        img = cv2.resize(img, (int(w * f), int(h * f)))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    quality = {
        "brightness": round(brightness, 1),
        "blur_variance": round(blur_var, 1),
        "too_dark": brightness < 40,
        "too_blurry": blur_var < 15,
        "note": ("画面过暗或过模糊，检测结果置信度降低" if (brightness < 40 or blur_var < 15)
                 else "画面质量正常"),
    }

    # 漂浮物：用大核背景估计水面，提取「比水面亮且凸出」的残差区域
    bg = cv2.medianBlur(gray, 31)
    residual = cv2.subtract(gray, bg)
    _, th = cv2.threshold(residual, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(th)
    total = float(h * w)
    detections = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < total * 0.0004 or area > total * 0.05:
            continue
        fill = area / float(cw * ch)               # 外接框填充率：死鱼躯体较实
        aspect = max(cw, ch) / max(1.0, float(min(cw, ch)))
        contrast = float(gray[labels == i].mean()) - brightness
        if fill < 0.35 or aspect > 8:
            continue
        score = min(1.0, (contrast / 60.0) * 0.5 + fill * 0.5)
        detections.append({
            "bbox": [int(x), int(y), int(cw), int(ch)],
            "area_ratio": round(area / total, 5),
            "fill_ratio": round(fill, 3),
            "contrast": round(contrast, 1),
            "score": round(score, 3),
        })
    detections.sort(key=lambda d: -d["score"])
    top = detections[:5]
    suspected = bool(top) and not (quality["too_dark"] or quality["too_blurry"])
    confidence = round(max(d["score"] for d in top) * 0.9, 3) if top else 0.0
    return {
        "ok": True,
        "model_version": MODEL_VERSION,
        "algorithm": "水面亮残差 + 连通域启发式（非深度模型），结果需人工确认",
        "quality": quality,
        "detections": top,
        "suspected": suspected,
        "confidence": confidence,
        "note": ("检测到疑似漂浮对象，已生成待确认事件" if suspected
                 else "未检测到疑似漂浮对象"),
    }


# ---------------------------------------------------------------- OCR（可选依赖）
def ocr_available():
    if shutil.which("tesseract") is None:
        return False, "未检测到 tesseract 可执行程序"
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "未安装 python 包 pytesseract"
    return True, None


def ocr_text(path, lang="chi_sim+eng"):
    """图片文字识别。依赖未安装时返回 (None, 明确原因)，不返回假文本。"""
    ok, why = ocr_available()
    if not ok:
        return None, (f"OCR 依赖未就绪：{why}。"
                      f"请安装 tesseract-ocr（含中文包 chi_sim）并 pip install pytesseract")
    import pytesseract
    if HAS_CV:
        img = _imread(path)
        if img is None:
            return None, "图片解码失败（请上传 jpg/png）"
    else:
        from PIL import Image
        img = Image.open(path)
    text = pytesseract.image_to_string(img, lang=lang)
    text = (text or "").strip()
    if not text:
        return None, "未识别出文字（图片可能过模糊或无文字内容）"
    return text, None
