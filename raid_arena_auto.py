import time
import random
import re
import threading
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import pyautogui
import pytesseract
import tkinter as tk
import json
from datetime import datetime, date
from tkinter import ttk

# ================= USER SETTINGS =================
POWER_LIMIT_K = 500.0
CALIBRATED_W=895
CALIBRATED_H=1045
CALIBRATED_LEFT=0
CALIBRATED_TOP=0
SIZE_TOLERANCE=35
REFRESH_COOLDOWN = 15 * 60
SCAN_INTERVAL = 1.0
POST_BATTLE_WAIT_MIN = 5.0
POST_BATTLE_WAIT_MAX = 10.0
MATCH_THRESHOLD = 0.78

# Raid window content size shown in the supplied screenshot: 895x1045.
# Coordinates below are relative to the Raid window client area.
REFERENCE_W = 895
REFERENCE_H = 1045

# Opponent rows (first row can be a previous Victory and therefore unavailable).
ROW_CENTERS_Y = [216, 303, 390, 477, 565, 652, 739, 826, 913, 1000]
POWER_X1, POWER_X2 = 500, 700
BATTLE_X = 790

START_X, START_Y = 782, 1002
CONTINUE_X, CONTINUE_Y = 447, 1004

# OCR executable is detected automatically on Windows.
TESSERACT_CMD = None

BASE = Path(__file__).resolve().parent

def setup_tesseract():
    """Automatically find the Windows Tesseract installation."""
    import shutil as _shutil
    candidates = [
        _shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        str(Path.home() / r"AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            return str(candidate)
    raise RuntimeError(
        "Tesseract OCR nije pronadjen. Pokreni INSTALL.bat kao Administrator, "
        "pa zatim ponovo START.bat."
    )
VICTORY_TEMPLATE = BASE / "victory.png"
DEFEAT_TEMPLATE = BASE / "defeat.png"
TEAM_SETUP_TEMPLATE = BASE / "team_setup.png"

running = False
paused = False
stop_requested = False
failed_rows = set()
last_refresh = 0.0
status_callback=print
log_callback=lambda msg: None
power_callback=lambda vals: None
stats_callback=lambda st: None
arena_stats_callback=lambda st: None
ALLOW_GEM_REFILL=False
TOKEN_WAIT_SECONDS=10*60
TOKEN_WAIT_UNTIL=0.0
TOKEN_WAIT_REMAINING=0.0
BLACKLIST_ENABLED={"Tekteon":True,"Mavara":True,"Sabrael":True,"Hekaton":True,"Solonar":True,"TMNT":True}
BLACKLIST_THRESHOLD=0.60
AUTO_POWER_LIMIT=False
ARENA_HISTORY_FILE=BASE/"arena_history.json"
CURRENT_BATTLE_POWER=None
OPTIMAL_TARGET_WINRATE=0.80

session_stats={"battles":0,"wins":0,"losses":0,"refreshes":0,"refills":0}

ERROR_SCREENSHOT_DIR=BASE/"error_screenshots"
ERROR_SCREENSHOT_COOLDOWN=60
_error_last_capture={}
_recent_log=deque(maxlen=30)
_error_capture_busy=False


def _safe_error_tag(reason):
    tag=re.sub(r"[^A-Za-z0-9_-]+","_",str(reason).strip())[:70].strip("_")
    return tag or "ERROR"


def capture_error_snapshot(reason, force=False):
    """Save RAID-only screenshot + diagnostic text. Same error is throttled for 60s."""
    global _error_capture_busy
    if _error_capture_busy:
        return None
    tag=_safe_error_tag(reason)
    now=time.time()
    if not force and now-_error_last_capture.get(tag,0)<ERROR_SCREENSHOT_COOLDOWN:
        return None
    _error_capture_busy=True
    try:
        ERROR_SCREENSHOT_DIR.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base=ERROR_SCREENSHOT_DIR/f"{stamp}_{tag}"
        # RAID window only, never the whole desktop.
        l,t,w,h=raid_region()
        im=pyautogui.screenshot(region=(l,t,w,h))
        png=base.with_suffix(".png")
        im.save(png)
        txt=base.with_suffix(".txt")
        txt.write_text(
            "Vreme: "+datetime.now().isoformat(timespec="seconds")+"\n"
            +"Razlog: "+str(reason)+"\n"
            +f"Raid region: {l},{t},{w},{h}\n\n"
            +"Poslednje log poruke:\n"
            +"\n".join(_recent_log),
            encoding="utf-8"
        )
        _error_last_capture[tag]=now
        return png
    except Exception:
        return None
    finally:
        _error_capture_busy=False



def status(msg):
    text=str(msg)
    status_callback(text)
    log_callback(text)
    _recent_log.append(datetime.now().strftime("%H:%M:%S")+"  "+text)

    # Capture only genuine error/failure states, not normal "not found" scanning messages.
    upper=text.upper()
    trigger=("GRESKA:" in upper or "ERROR:" in upper or
             "CONFIRM NIJE PRONAĐEN" in upper or
             "CONFIRM NIJE PRONADJEN" in upper)
    if trigger and not upper.startswith("SCREENSHOT GREŠKE"):
        path=capture_error_snapshot(text)
        if path:
            notice=f"Screenshot greške sačuvan: {path}"
            status_callback(notice)
            log_callback(notice)
            _recent_log.append(datetime.now().strftime("%H:%M:%S")+"  "+notice)

def update_stats():
    stats_callback(dict(session_stats))


def raid_region():
    """Return exact Raid: Shadow Legends outer-window bounds."""
    w=get_raid_window()
    if w.isMinimized:
        w.restore()
        time.sleep(.5)
    width,height=int(w.width),int(w.height)
    if width<300 or height<300:
        raise RuntimeError(f"Raid prozor ima nevalidnu velicinu {width}x{height}.")
    return int(w.left),int(w.top),width,height

def get_raid_window():
    allwins=pyautogui.getAllWindows()
    exact=[w for w in allwins if (w.title or "").strip().lower()=="raid: shadow legends"]
    if not exact:
        exact=[w for w in allwins if "shadow legends" in (w.title or "").lower()
               and "arena auto" not in (w.title or "").lower()]
    valid=[w for w in exact if getattr(w,"width",0)>300 and getattr(w,"height",0)>300]
    if not valid:
        raise RuntimeError("Raid: Shadow Legends prozor nije pronadjen.")
    return max(valid,key=lambda x:x.width*x.height)


def auto_size_raid():
    """Move/resize Raid to the exact outer-window rectangle used for calibration."""
    w=get_raid_window()
    if w.isMinimized:
        w.restore()
        time.sleep(.4)
    try:
        w.moveTo(CALIBRATED_LEFT,CALIBRATED_TOP)
        time.sleep(.15)
        w.resizeTo(CALIBRATED_W,CALIBRATED_H)
        time.sleep(.45)
    except Exception as e:
        raise RuntimeError(f"Ne mogu automatski da podesim Raid prozor: {e}")
    return int(w.left),int(w.top),int(w.width),int(w.height)


def show_calibration_frame(root):
    """Transparent always-on-top rectangle showing the expected Raid outer bounds."""
    frame=tk.Toplevel(root)
    frame.title("Raid kalibracioni okvir")
    frame.geometry(f"{CALIBRATED_W}x{CALIBRATED_H}+{CALIBRATED_LEFT}+{CALIBRATED_TOP}")
    frame.overrideredirect(True)
    frame.attributes("-topmost",True)
    try:
        frame.attributes("-transparentcolor","white")
    except Exception:
        frame.attributes("-alpha",0.28)
    frame.configure(bg="white")

    # Visible border and instructions; center stays transparent on Windows.
    c=tk.Canvas(frame,width=CALIBRATED_W,height=CALIBRATED_H,bg="white",highlightthickness=0)
    c.pack(fill="both",expand=True)
    c.create_rectangle(3,3,CALIBRATED_W-4,CALIBRATED_H-4,outline="red",width=6)
    c.create_rectangle(12,12,390,55,fill="black",outline="red",width=2)
    c.create_text(25,33,anchor="w",fill="white",
                  text=f"RAID OKVIR {CALIBRATED_W} x {CALIBRATED_H} — poravnaj ivice igre")
    frame.after(12000,lambda: frame.destroy() if frame.winfo_exists() else None)
    return frame


def shot():
    l, t, w, h = raid_region()
    try:
        im = pyautogui.screenshot(region=(l, t, w, h))
    except Exception as e:
        raise RuntimeError(f"Screenshot Raid prozora nije uspeo ({l},{t},{w},{h}): {e}")
    arr = np.array(im)
    if arr.size == 0 or arr.shape[0] < 10 or arr.shape[1] < 10:
        raise RuntimeError(f"Screenshot je prazan: {arr.shape}. Drzi Raid na glavnom monitoru.")
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), (l, t, w, h)

def scale_xy(x, y, reg):
    l, t, w, h = reg
    return l + int(x * w / REFERENCE_W), t + int(y * h / REFERENCE_H)


def click_ref(x, y, jitter=3):
    reg = raid_region()
    sx, sy = scale_xy(x, y, reg)
    pyautogui.click(sx + random.randint(-jitter, jitter),
                    sy + random.randint(-jitter, jitter))


def template_score(frame, path):
    if not path.exists():
        return 0.0
    tpl = cv2.imread(str(path))
    if tpl is None:
        return 0.0
    # Template may be a full screenshot. Resize to current window and compare.
    fh, fw = frame.shape[:2]
    th, tw = tpl.shape[:2]
    if tw > fw or th > fh:
        tpl = cv2.resize(tpl, (fw, fh))
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    return float(res.max())


def detect_result(frame):
    # Robust result detection using characteristic header colors/text area + OCR.
    h, w = frame.shape[:2]
    crop = frame[int(.14*h):int(.36*h), int(.25*w):int(.75*w)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.size == 0:
        return None
    txt = pytesseract.image_to_string(gray, config="--psm 6").upper()
    if "VICTORY" in txt:
        return "victory"
    if "DEFEAT" in txt:
        return "defeat"
    return None


def on_arena_list(frame):
    return len(find_battle_buttons(frame)) >= 1

def on_team_setup(frame):
    h, w = frame.shape[:2]
    # Expected Start-button zone from supplied Team Setup screenshot.
    sx, sy = w / REFERENCE_W, h / REFERENCE_H
    x1, x2 = int(695*sx), min(w, int(880*sx))
    y1, y2 = max(0, int(955*sy)), min(h, int(1040*sy))
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([12, 70, 70]), np.array([48, 255, 255]))
    if (mask > 0).mean() > 0.10:
        return True
    txt = pytesseract.image_to_string(crop, config="--psm 6").upper()
    return "START" in txt

def parse_power(text):
    """Return Team Power normalized to K. Supports 97,444 / 459.4K / 1.10M."""
    if not text:
        return None
    raw=str(text).upper().strip().replace(" ", "")
    # OCR sometimes uses comma as decimal separator before K/M.
    if re.search(r"\d,\d{1,2}[KM]", raw):
        raw=raw.replace(",", ".")
    else:
        raw=raw.replace(",", "")
    m=re.search(r"(\d+(?:\.\d+)?)\s*([KM]?)", raw)
    if not m:
        return None
    try:
        v=float(m.group(1))
    except ValueError:
        return None
    unit=m.group(2)
    if unit=="M":
        return v*1000.0
    # Plain values over 10,000 are absolute power, normalize to K.
    if not unit and v>=10000:
        return v/1000.0
    return v

def read_row_power(frame, row_y):
    h, w = frame.shape[:2]
    sx = w / REFERENCE_W
    sy = h / REFERENCE_H
    x1, x2 = int(POWER_X1*sx), int(POWER_X2*sx)
    y1, y2 = int((row_y-18)*sy), int((row_y+18)*sy)
    crop = frame[max(0,y1):min(h,y2), max(0,x1):min(w,x2)]
    if crop.size == 0:
        return None, ""
    # Enlarge + threshold for Team Power text.
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.7, beta=15)
    txt = pytesseract.image_to_string(
        gray, config="--psm 7 -c tessedit_char_whitelist=TeamPower:0123456789.,KMkm"
    )
    # Prefer number near K/M.
    matches = re.findall(r"\d+(?:\.\d+)?\s*[KMkm]", txt)
    if matches:
        return parse_power(matches[-1]), txt.strip()
    return None, txt.strip()


def find_battle_buttons(frame):
    """Find all visible Battle buttons using the supplied button image."""
    tpl = cv2.imread(str(BASE / "battle_button.png"))
    if tpl is None:
        return []
    fh, fw = frame.shape[:2]
    th, tw = tpl.shape[:2]

    # Try several scales because Windows DPI/window borders can change apparent size.
    found = []
    for scale in (0.80, 0.90, 1.00, 1.10, 1.20):
        ntw, nth = int(tw*scale), int(th*scale)
        if ntw < 20 or nth < 10 or ntw >= fw or nth >= fh:
            continue
        rt = cv2.resize(tpl, (ntw,nth), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(frame, rt, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= 0.62)
        for x,y in zip(xs,ys):
            found.append((float(res[y,x]), x+ntw//2, y+nth//2))

    # NMS: keep strongest detections separated vertically/horizontally.
    found.sort(reverse=True)
    kept=[]
    for score,x,y in found:
        if all(abs(x-kx)>35 or abs(y-ky)>25 for _,kx,ky in kept):
            kept.append((score,x,y))
    kept.sort(key=lambda z:z[2])
    return kept[:12]


def row_has_battle(frame, row_y):
    # Compatibility helper: nearest template detection to expected row.
    return any(abs(y-row_y) < 35 for _,x,y in find_battle_buttons(frame))


def _parse_power_token(token):
    """
    Supports:
      97,444  -> 97.444K
      97.44K  -> 97.44K
      459K / 459.4K / 459.40K / 464.53K
      1.02M   -> 1020K
    K may be missing because Raid can display the raw value / OCR can drop it.
    """
    raw=token.upper().replace(" ","")
    unit="M" if "M" in raw else ("K" if "K" in raw else "")
    num=re.sub(r"[^0-9.,]","",raw)
    if not num:
        return None

    # Raid may show 97,444 for 97.444K. For this Team Power crop,
    # a comma followed by exactly 3 digits is a thousands separator.
    if "," in num and "." not in num:
        parts=num.split(",")
        if len(parts)==2 and len(parts[1])==3:
            value=float(parts[0]+"."+parts[1])
        else:
            value=float(num.replace(",","."))
    else:
        value=float(num.replace(",","."))

    if unit=="M":
        value*=1000.0
    elif unit=="" and value>=10000:
        # Raw integer OCR such as 97444 -> 97.444K.
        value/=1000.0

    return value if 1 <= value <= 10000 else None

def _ocr_power_crop(crop):
    """Read the same narrow Team Power strip with several image treatments."""
    if crop.size==0:
        return None,""

    big=cv2.resize(crop,None,fx=5,fy=5,interpolation=cv2.INTER_CUBIC)
    gray=cv2.cvtColor(big,cv2.COLOR_BGR2GRAY)

    variants=[
        gray,
        cv2.convertScaleAbs(gray,alpha=1.8,beta=10),
        cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1],
    ]

    readings=[]
    raw_text=[]
    cfg="--psm 7 -c tessedit_char_whitelist=TeamPower:0123456789.,KMkm"
    token_re=r"\d{1,7}(?:[.,]\d{1,3})?\s*[KMkm]?"

    for img in variants:
        txt=pytesseract.image_to_string(img,config=cfg).strip()
        raw_text.append(txt)
        vals=re.findall(token_re,txt)
        for tok in reversed(vals):
            v=_parse_power_token(tok)
            if v is not None:
                readings.append(v)
                break

    if not readings:
        return None," | ".join(raw_text)

    # Exact/near agreement wins. This fixes cases where one OCR pass drops a digit,
    # e.g. 260K -> 20K, without adding a second pre-Battle safety stage.
    for v in readings:
        agree=[x for x in readings if abs(x-v) <= max(0.8, v*0.025)]
        if len(agree)>=2:
            return float(np.median(agree))," | ".join(raw_text)

    # No consensus: do not invent a low value. Mark this row unreadable.
    return None," | ".join(raw_text)


def read_power_left_of_button(frame,bx,by):
    h,w=frame.shape[:2]
    # Raid renders Team Power in a narrow strip directly below the champion cards.
    # Keep OCR away from hero levels, opponent rating and the Battle cost.
    x1=max(0,bx-275); x2=max(1,bx-62)
    y1=max(0,by+20); y2=min(h,by+43)
    crop=frame[y1:y2,x1:x2]
    return _ocr_power_crop(crop)


def read_all_team_powers(frame,buttons):
    # Read each row independently from its fixed Team Power strip.
    # This is intentionally not one large OCR pass: large crops can merge/drop digits.
    out={}
    for j,(_,bx,by) in enumerate(buttons):
        v,txt=read_power_left_of_button(frame,bx,by)
        if v is not None:
            out[j]=(v,txt)
    return out


def _hero_face_template(name):
    """Use only the stable portrait area; ignore stars, level, affinity and most border."""
    img=cv2.imread(str(BASE/f"blacklist_{name.lower()}.png"))
    if img is None:
        return None
    h,w=img.shape[:2]
    # Deliberately remove top stars and bottom level/icons.
    x1=int(w*.10); x2=int(w*.90)
    y1=int(h*.24); y2=int(h*.76)
    face=img[y1:y2,x1:x2]
    if face.size==0:
        return None
    return cv2.cvtColor(face,cv2.COLOR_BGR2GRAY)


def row_blacklisted_hero(frame,bx,by):
    """
    Search only the enemy champion-card strip belonging to this Battle row.
    Returns (hero_name, score) or (None, 0).
    """
    enabled=[n for n,v in BLACKLIST_ENABLED.items() if v]
    if not enabled:
        return None,0.0

    h,w=frame.shape[:2]
    # Enemy cards sit immediately left of Battle; keep the search inside this row.
    x1=max(0,bx-325); x2=max(1,bx-65)
    y1=max(0,by-55); y2=min(h,by+20)
    roi=frame[y1:y2,x1:x2]
    if roi.size==0:
        return None,0.0
    gray=cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY)

    best_name=None; best_score=0.0
    for name in enabled:
        template_names = [name]
        if name == "TMNT":
            template_names = ["TMNT1","TMNT2","TMNT3","TMNT4"]
        for template_name in template_names:
            tpl=_hero_face_template(template_name)
            if tpl is None:
                continue
            th,tw=tpl.shape[:2]
            for scale in (.78,.86,.94,1.0,1.08,1.16,1.24):
                nw,nh=max(8,int(tw*scale)),max(8,int(th*scale))
                if nw>=gray.shape[1] or nh>=gray.shape[0]:
                    continue
                rt=cv2.resize(tpl,(nw,nh),interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_CUBIC)
                res=cv2.matchTemplate(gray,rt,cv2.TM_CCOEFF_NORMED)
                score=float(cv2.minMaxLoc(res)[1])
                if score>best_score:
                    best_name,best_score=name,score

    if best_score>=BLACKLIST_THRESHOLD:
        return best_name,best_score
    return None,best_score


def scan_candidates(frame):
    buttons=find_battle_buttons(frame)
    status(f"Battle dugmadi: {len(buttons)} | citam Team Power...")
    bulk=read_all_team_powers(frame,buttons); candidates=[]
    for idx,(score,bx,by) in enumerate(buttons):
        if idx in failed_rows: continue

        blocked,hero_score=row_blacklisted_hero(frame,bx,by)
        if blocked:
            status(f"Red {idx+1}: PRESKOCEN - blacklist {blocked} ({hero_score:.2f})")
            continue

        power,raw=bulk.get(idx,(None,""))
        if power is None: power,raw=read_power_left_of_button(frame,bx,by)
        if power is not None:
            status(f"Red {idx+1}: {power:g}K")
            if power<effective_power_limit(): candidates.append((power,idx,bx,by))
        else: status(f"Red {idx+1}: Team Power nije procitan")
    return candidates

def load_arena_history():
    try:
        if ARENA_HISTORY_FILE.exists():
            data=json.loads(ARENA_HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data,list) else []
    except Exception:
        pass
    return []

def save_arena_history(hist):
    try:
        ARENA_HISTORY_FILE.write_text(json.dumps(hist[-2000:],indent=2),encoding="utf-8")
    except Exception as e:
        status(f"History save greška: {e}")

def record_arena_result(power,result):
    if power is None or result not in ("victory","defeat"):
        return
    hist=load_arena_history()
    hist.append({"power_k":round(float(power),3),"result":result,"time":int(time.time())})
    save_arena_history(hist)
    try:
        arena_stats_callback(arena_power_statistics())
    except Exception:
        pass

def reset_arena_statistics():
    """Delete only persistent Arena W/L/WR/optimal-limit learning data."""
    try:
        save_arena_history([])
        arena_stats_callback(arena_power_statistics())
        return True
    except Exception:
        return False

def arena_power_statistics():
    """Return samples, overall win rate and conservative optimal power ceiling."""
    hist=load_arena_history()
    clean=[x for x in hist if isinstance(x,dict) and x.get("result") in ("victory","defeat") and isinstance(x.get("power_k"),(int,float))]
    n=len(clean)
    if not n:
        return {"samples":0,"wins":0,"winrate":None,"optimal":None}
    wins=sum(x["result"]=="victory" for x in clean)
    # Need enough evidence before automatic limit becomes active.
    if n < 15:
        return {"samples":n,"wins":wins,"winrate":wins/n,"optimal":None}

    # Test 25K ceilings. Require >=8 fights at/below a ceiling and >=80% observed wins.
    maxp=max(x["power_k"] for x in clean)
    ceilings=[float(x) for x in range(100, int(maxp//25)*25+26,25)]
    good=[]
    for ceiling in ceilings:
        subset=[x for x in clean if x["power_k"]<=ceiling]
        if len(subset)>=8:
            wr=sum(x["result"]=="victory" for x in subset)/len(subset)
            if wr>=OPTIMAL_TARGET_WINRATE:
                good.append((ceiling,len(subset),wr))
    optimal=max((x[0] for x in good),default=None)
    return {"samples":n,"wins":wins,"winrate":wins/n,"optimal":optimal}

def effective_power_limit():
    st=arena_power_statistics()
    if AUTO_POWER_LIMIT and st["optimal"] is not None:
        return st["optimal"]
    return POWER_LIMIT_K

def choose_candidate(candidates):
    if not candidates:
        return None
    # Lowest power first. Randomize only effectively tied values (within 0.5K).
    minimum = min(x[0] for x in candidates)
    tied = [x for x in candidates if abs(x[0] - minimum) <= 0.5]
    return random.choice(tied)


def wait_for(predicate, timeout, label):
    started = time.time()
    while not stop_requested and time.time() - started < timeout:
        while paused and not stop_requested:
            time.sleep(.2)
        frame, _ = shot()
        result = predicate(frame)
        if result:
            return result
        status(label)
        time.sleep(SCAN_INTERVAL)
    return None


def read_arena_keys(frame):
    """
    Read Arena token counter such as 4/10 from the top-right.
    Uses a broad top-right crop because window/DPI can vary.
    Returns integer available keys, or None if OCR is uncertain.
    """
    h,w=frame.shape[:2]
    # Counter is around x=650..735, y=28..65 in the supplied 895x1045 layout.
    x1=int(w*0.69); x2=int(w*0.84)
    y1=int(h*0.015); y2=int(h*0.075)
    crop=frame[y1:y2,x1:x2]
    if crop.size == 0:
        return None
    crop=cv2.resize(crop,None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    # OCR both normal and thresholded variants.
    texts=[]
    texts.append(pytesseract.image_to_string(
        gray, config="--psm 7 -c tessedit_char_whitelist=0123456789/"
    ))
    _,thr=cv2.threshold(gray,140,255,cv2.THRESH_BINARY)
    texts.append(pytesseract.image_to_string(
        thr, config="--psm 7 -c tessedit_char_whitelist=0123456789/"
    ))
    for txt in texts:
        m=re.search(r"(\d+)\s*/\s*10",txt)
        if m:
            v=int(m.group(1))
            if 0 <= v <= 10:
                return v
    return None


def refresh_available(frame=None):
    """Refresh is allowed ONLY when the supplied FREE refresh_button is visible."""
    if frame is None:
        frame,_=shot()
    # Explicit GEM-cost veto wins even if another template has a weak match.
    paid=find_template_center(frame,"refresh_gem_forbidden.png",0.72,
                              scales=(.80,.90,.95,1.0,1.05,1.10,1.20))
    if paid:
        return False
    free=find_template_center(frame,"refresh_button.png",0.62,
                              scales=(.65,.72,.80,.88,.94,1.0,1.06,1.14,1.24,1.35))
    return free is not None


def do_refresh(frame=None):
    global last_refresh, failed_rows
    if frame is None:
        frame,_=shot()
    hit=find_template_center(frame,"refresh_button.png",0.62,
                             scales=(.65,.72,.80,.88,.94,1.0,1.06,1.14,1.24,1.35))
    paid=find_template_center(frame,"refresh_gem_forbidden.png",0.72,
                              scales=(.80,.90,.95,1.0,1.05,1.10,1.20))
    if not hit or paid:
        status("Refresh nije FREE -> ne klikćem.")
        return False
    _,reg=shot()
    pyautogui.click(reg[0]+hit[1],reg[1]+hit[2])
    status("Nema dozvoljenog protivnika; FREE Refresh potvrđen -> Refresh")
    last_refresh=time.time()
    session_stats["refreshes"]+=1
    update_stats()
    failed_rows.clear()
    time.sleep(3)
    return True


def find_template_center(frame, filename, threshold=0.72, scales=(0.75,0.85,0.95,1.0,1.05,1.15,1.25)):
    tpl=cv2.imread(str(BASE/filename))
    if tpl is None:
        return None
    fh,fw=frame.shape[:2]
    th,tw=tpl.shape[:2]
    best=None
    for sc in scales:
        nw,nh=int(tw*sc),int(th*sc)
        if nw<10 or nh<10 or nw>=fw or nh>=fh:
            continue
        rt=cv2.resize(tpl,(nw,nh),interpolation=cv2.INTER_AREA)
        res=cv2.matchTemplate(frame,rt,cv2.TM_CCOEFF_NORMED)
        _,mx,_,loc=cv2.minMaxLoc(res)
        if best is None or mx>best[0]:
            best=(mx,loc[0]+nw//2,loc[1]+nh//2)
    if best and best[0]>=threshold:
        return best
    return None


def popup_text(frame):
    """OCR only the central refill-dialog area."""
    h,w=frame.shape[:2]
    crop=frame[int(.10*h):int(.78*h),int(.14*w):int(.86*w)]
    if not crop.size:
        return ""
    try:
        return pytesseract.image_to_string(crop,config="--psm 6").upper()
    except Exception:
        return ""


def find_free_refill_popup(frame):
    """
    Return the matched free-refill popup including its scaled geometry.
    The supplied popup templates already contain the Confirm button, so this
    gives us a reliable Confirm position even when the smaller button template
    does not independently match.
    """
    fh,fw=frame.shape[:2]
    best=None
    for name in ("arena_refill_popup.png","arena_tokens_popup.png"):
        tpl=cv2.imread(str(BASE/name))
        if tpl is None:
            continue
        th,tw=tpl.shape[:2]
        for sc in (0.75,0.82,0.90,0.95,1.0,1.05,1.10,1.18,1.25):
            nw,nh=int(tw*sc),int(th*sc)
            if nw<20 or nh<20 or nw>=fw or nh>=fh:
                continue
            rt=cv2.resize(tpl,(nw,nh),interpolation=cv2.INTER_AREA)
            res=cv2.matchTemplate(frame,rt,cv2.TM_CCOEFF_NORMED)
            _,mx,_,loc=cv2.minMaxLoc(res)
            if best is None or mx>best[0]:
                best=(float(mx),name,loc[0],loc[1],nw,nh)
    return best if best and best[0]>=0.62 else None


def free_refill_popup_visible(frame):
    return find_free_refill_popup(frame) is not None


def find_confirm_button(frame):
    """
    First try confirm_button.png directly. If that fails, use the Confirm
    position INSIDE the already-recognized refill popup. Both supplied popup
    templates visibly contain Confirm at the bottom-center.
    """
    direct=find_template_center(
        frame,"confirm_button.png",0.52,
        scales=(0.70,0.78,0.85,0.92,1.0,1.08,1.16,1.25,1.35)
    )
    if direct:
        return direct

    popup=find_free_refill_popup(frame)
    if not popup:
        return None
    score,name,x,y,w,h=popup

    # Confirm is bottom-center in both supplied popup templates.
    # Use the popup geometry itself instead of guessing an absolute screen point.
    cx=x + w//2
    cy=y + int(h*0.85)
    return (score,cx,cy)


def handle_free_refill():
    """
    Exact refill sequence:
      arena_refill_popup / arena_tokens_popup
      -> Confirm
      -> wait for refill popup to disappear
      -> TEAM_SETUP
      -> Battle/Start
    """
    status("Refill popup pronađen -> klik CONFIRM")
    end=time.time()+12
    while time.time()<end:
        frame,reg=shot()
        confirm=find_confirm_button(frame)
        if confirm:
            click_screen_relative(confirm,reg)
            status("CONFIRM kliknut")
            time.sleep(1.2)

            # Verify that Confirm actually closed the refill dialog.
            gone_end=time.time()+8
            while time.time()<gone_end:
                fr,_=shot()
                if not free_refill_popup_visible(fr):
                    break
                time.sleep(.25)
            else:
                status("Refill popup je i dalje otvoren posle CONFIRM.")
                return False

            if not wait_team_setup_and_start(
                15, "CONFIRM završen -> čekam TEAM_SETUP pa BATTLE/START..."
            ):
                status("Posle CONFIRM nije pronađen TEAM_SETUP.")
                return False

            status("Refill završen: CONFIRM -> BATTLE/START")
            return True
        time.sleep(.20)

    status("CONFIRM nije pronađen — ne nastavljam bitku.")
    return False


def free_refill_confirm(frame):
    return find_confirm_button(frame) if free_refill_popup_visible(frame) else None

def find_gem_get_and_use(frame, threshold=0.76):
    """
    Detect the GEM-specific paid button captured from the user's actual popup:
    red gem + 40 + Get and Use.
    This avoids classifying by the generic popup frame shared with FREE refill.
    """
    return find_template_center(
        frame,"gem_get_and_use.png",threshold,
        scales=(0.90,0.95,1.0,1.05,1.10)
    )


def classify_refill_popup_once(frame):
    """
    V33 priority:
      1) GEM-specific '40 / Get and Use' button
      2) FREE refill templates
    Generic gem_refill_popup frame is NOT used to decide GEM vs FREE.
    """
    gem_button=find_gem_get_and_use(frame,0.76)
    if gem_button:
        return "gem", gem_button

    free=find_free_refill_popup(frame)
    if free:
        return "free", free

    return None, None


def classify_refill_popup(frame=None, checks=3, interval=0.22):
    """
    Lightweight consensus to reduce UI/game lag.
    Three fresh screenshots; 2/3 matching votes are required.
    GEM vote uses only the specific 40/Get and Use button.
    """
    votes=[]
    details=[]

    for i in range(checks):
        if stop_requested:
            return None, {"votes":votes,"reason":"stopped"}

        current,_=shot()
        kind,hit=classify_refill_popup_once(current)
        votes.append(kind); details.append(hit)

        if i < checks-1:
            time.sleep(interval)

    free_count=votes.count("free")
    gem_count=votes.count("gem")
    status(f"Popup provera: FREE={free_count}/{checks}, GEM={gem_count}/{checks}")

    if gem_count >= 2 and gem_count > free_count:
        for k,h in reversed(list(zip(votes,details))):
            if k=="gem":
                return "gem",h

    if free_count >= 2 and free_count > gem_count:
        for k,h in reversed(list(zip(votes,details))):
            if k=="free":
                return "free",h

    status("Popup nejasan -> ne klikćem ništa.")
    return None, {"votes":votes,"reason":"ambiguous"}


def gem_refill_visible(frame):
    """Paid refill is true only when the specific 40/Get and Use button is visible."""
    return find_gem_get_and_use(frame,0.76) is not None


def click_template(frame,reg,filename,threshold=0.68):
    hit=find_template_center(frame,filename,threshold)
    if not hit:
        return False
    click_screen_relative(hit,reg)
    return True


def close_gem_refill_and_team_setup():
    """
    V34 exact blocked-GEM navigation:
      1. Verify the paid 40/Get and Use popup.
      2. Click the round X inside that GEM popup.
      3. Wait for TEAM_SETUP.
      4. Click the second X to return to the Arena opponent list.
    No coordinate fallback is permitted.
    """
    frame,reg=shot()

    # Never close a verified FREE refill as GEM.
    if free_refill_popup_visible(frame) and not gem_refill_visible(frame):
        status("FREE refill je na ekranu -> GEM X rutina nije dozvoljena.")
        return False

    if not gem_refill_visible(frame):
        status("40 / Get and Use nije potvrđen -> ne klikćem ništa.")
        return False

    # First X: the circular close button belonging to the GEM popup.
    first_x=find_template_center(
        frame,"gem_popup_close_exact.png",0.72,
        scales=(0.85,0.90,0.95,1.0,1.05,1.10,1.15)
    )
    if not first_x:
        status("Prvi GEM popup X nije pronađen -> ne klikćem ništa.")
        return False

    click_screen_relative(first_x,reg)
    status("GEM OFF -> kliknut prvi X na GEM popup-u")
    time.sleep(0.8)

    # Wait until Team Setup is visible before the second X.
    if not wait_for(on_team_setup,8,"Čekam TEAM_SETUP posle prvog X..."):
        status("TEAM_SETUP nije potvrđen -> drugi X nije kliknut.")
        return False

    frame,reg=shot()
    second_x=find_template_center(
        frame,"team_setup_close_exact.png",0.72,
        scales=(0.85,0.90,0.95,1.0,1.05,1.10,1.15)
    )
    if not second_x:
        # Existing supplied template is a safe second exact option.
        second_x=find_template_center(
            frame,"team_setup_close.png",0.68,
            scales=(0.85,0.90,0.95,1.0,1.05,1.10,1.15)
        )
    if not second_x:
        status("Drugi TEAM_SETUP X nije pronađen -> ne klikćem naslepo.")
        return False

    click_screen_relative(second_x,reg)
    status("Kliknut drugi X -> povratak na glavni Arena ekran")
    time.sleep(0.8)

    if wait_for(on_arena_list,8,"Čekam glavni Arena ekran..."):
        status("Arena lista potvrđena -> nastavljam skeniranje protivnika")
        return True

    status("Arena lista još nije potvrđena; nema dodatnih nasumičnih klikova.")
    return False

def token_popup_visible(frame):
    """Compatibility wrapper: free refill detection only."""
    return free_refill_confirm(frame)


def click_screen_relative(pos, reg):
    _,x,y=pos
    l,t,_,_=reg
    pyautogui.click(l+x+random.randint(-3,3),t+y+random.randint(-3,3))


def wait_team_setup_and_start(timeout=15, message="Cekam TEAM_SETUP..."):
    """
    Wait for the already supplied TEAM_SETUP screen and click its Start button.
    This exact same routine is used before the battle and again after token Confirm.
    """
    ok = wait_for(on_team_setup, timeout, message)
    if not ok:
        return False
    status("TEAM_SETUP prepoznat -> klik Start")
    click_ref(START_X, START_Y)
    return True


def wait_for_token_regeneration(seconds=TOKEN_WAIT_SECONDS):
    """
    Wait after a verified paid GEM popup was rejected because GEM Use is OFF.
    This means neither of the FREE refill rewards (5/10 tokens) was available
    in the current flow. Countdown is interruptible by STOP and manual PAUSE.
    If GEM Use is enabled during the countdown, waiting ends immediately.
    """
    global TOKEN_WAIT_UNTIL,TOKEN_WAIT_REMAINING
    TOKEN_WAIT_REMAINING=0.0
    TOKEN_WAIT_UNTIL=time.time()+seconds
    status("Nema tokena/free refilla -> sledeći pokušaj za 10 min.")
    last_shown=None

    while not stop_requested:
        # GEM permission overrides the 61-minute wait immediately.
        if ALLOW_GEM_REFILL is True:
            TOKEN_WAIT_UNTIL=0.0
            status("GEM Use uključen -> prekidam čekanje tokena.")
            return True

        # Manual PAUSE freezes the countdown rather than consuming it.
        if paused:
            pause_started=time.time()
            while paused and not stop_requested:
                if ALLOW_GEM_REFILL is True:
                    TOKEN_WAIT_UNTIL=0.0
                    status("GEM Use uključen -> prekidam čekanje tokena.")
                    return True
                time.sleep(0.25)
            if stop_requested:
                TOKEN_WAIT_UNTIL=0.0
                return False
            TOKEN_WAIT_UNTIL += time.time()-pause_started

        remaining=max(0,int(TOKEN_WAIT_UNTIL-time.time()))
        if remaining<=0:
            TOKEN_WAIT_UNTIL=0.0
            status("10 min čekanje završeno -> jedan novi Arena pokušaj.")
            return True
        time.sleep(1.0)

    TOKEN_WAIT_UNTIL=0.0
    return False



def battle(row_idx, button_pos, selected_power=None):
    global CURRENT_BATTLE_POWER
    CURRENT_BATTLE_POWER=selected_power
    global failed_rows
    status(f"Klik Battle: {row_idx+1}")
    session_stats["battles"]+=1
    update_stats()
    frame, reg = shot()
    bx, by = button_pos
    l,t,w,h = reg
    # bx/by are already coordinates in the captured Raid window.
    pyautogui.click(l+bx+random.randint(-3,3), t+by+random.randint(-3,3))
    time.sleep(2)

    if not wait_team_setup_and_start(15, "Cekam TEAM_SETUP pre prve bitke..."):
        status("TEAM_SETUP nije prepoznat. Vracam se na skeniranje.")
        return
    time.sleep(2)

    # V29 refill routing: classify the popup BEFORE any click.
    frame, reg = shot()
    refill_kind,refill_hit = classify_refill_popup(frame, checks=3, interval=0.22)

    # If a refill-like screen is present but consensus is not strong enough,
    # fail safe: do not click anything. The worker can rescan later.
    if refill_kind is None:
        latest,_ = shot()
        # If any supplied refill template is even weakly present, treat as ambiguous popup.
        weak_free = find_free_refill_popup(latest)
        weak_gem = find_gem_get_and_use(latest,0.68)
        if weak_free or weak_gem:
            status("Refill popup je nejasan posle 3 provere -> bez klika.")
            return

    if refill_kind == "free":
        # Hard rule: FREE popup -> Confirm. X is forbidden in this branch.
        status("FREE Arena refill -> CONFIRM (X zabranjen)")
        if not handle_free_refill():
            return
        session_stats["refills"]+=1
        update_stats()
        time.sleep(3)

    elif refill_kind == "gem" or gem_refill_visible(frame):
        # V31: ALLOW_GEM_REFILL is synchronized LIVE with the checkbox.
        # Snapshot it immediately before the paid action.
        gem_allowed_now = (ALLOW_GEM_REFILL is True)
        if not gem_allowed_now:
            status("GEM USE = OFF -> izlazim iz GEM refill-a bez trošenja")
            returned_to_arena=close_gem_refill_and_team_setup()
            if returned_to_arena:
                status("Nema dostupnog FREE refill-a (5/10) -> pauza 10 min")
                wait_for_token_regeneration()
            return

        # Paid action exists ONLY below the explicit live True gate.
        status("GEM USE = ON -> klikujem potvrđeni 40 / Get and Use")
        latest,latest_reg=shot()
        paid_button=find_gem_get_and_use(latest,0.76)
        if not paid_button:
            status("Get and Use više nije potvrđen -> NE KLIKĆEM.")
            return
        click_screen_relative(paid_button,latest_reg)
        session_stats["refills"]+=1
        update_stats()
        time.sleep(1.2)
        if not wait_team_setup_and_start(15,"Gem refill iskorišćen -> čekam TEAM_SETUP..."):
            status("TEAM_SETUP se nije pojavio posle gem refilla.")
            return
        time.sleep(1.0)

    result = wait_for(detect_result, 300, "Borba traje...")
    if result is None:
        status("Rezultat nije prepoznat u roku od 5 min.")
        return

    status("VICTORY" if result == "victory" else "DEFEAT")
    record_arena_result(CURRENT_BATTLE_POWER,result)
    if result=="victory": session_stats["wins"]+=1
    else:
        session_stats["losses"]+=1
        failed_rows.add(row_idx)
    update_stats()

    # First result screen.
    time.sleep(random.uniform(0.8, 1.5))
    click_ref(CONTINUE_X, CONTINUE_Y, jitter=6)
    time.sleep(2.0)
    # Second result screen: Return to Arena / continue.
    click_ref(CONTINUE_X, CONTINUE_Y, jitter=6)

    wait_for(on_arena_list, 20, "Vracam se u Arenu...")
    delay=random.choice((1.0, 3.0))
    status(f"Arena spremna. Sledece skeniranje za {delay:.1f}s...")
    time.sleep(delay)


def worker():
    global running, stop_requested,TOKEN_WAIT_REMAINING
    running = True
    stop_requested = False
    try:
        tess = setup_tesseract()
        l,t,w,h=raid_region()
        if abs(w-CALIBRATED_W)>SIZE_TOLERANCE or abs(h-CALIBRATED_H)>SIZE_TOLERANCE:
            status(f"UPOZORENJE: Raid {w}x{h}; kalibrisano {CALIBRATED_W}x{CALIBRATED_H}")
        else: status(f"Raid velicina OK: {w}x{h}")
        time.sleep(1.2)
        status("Pokrenuto. F8 = STOP.")
        while not stop_requested:
            if TOKEN_WAIT_REMAINING>0:
                remaining=TOKEN_WAIT_REMAINING
                TOKEN_WAIT_REMAINING=0.0
                wait_for_token_regeneration(remaining)
                continue
            while paused and not stop_requested:
                time.sleep(.2)

            frame, _ = shot()
            if not on_arena_list(frame):
                visible = len(find_battle_buttons(frame))
                status(f"Cekam Arena listu... Battle detektovano: {visible}")
                time.sleep(1)
                continue

            # Uvek trazimo kandidata <500K. Ako nema tokena, Raid ce nakon
            # pokusaja ulaska prikazati token popup; njega obradjujemo u battle().
            status("Trazim najslabijeg kandidata <500K...")
            candidates = scan_candidates(frame)
            choice = choose_candidate(candidates)
            if choice:
                power, idx, bx, by = choice
                status(f"Najslabiji kandidat: Battle {idx+1}, {power:.2f}K")
                battle(idx, (bx, by), power)
                continue

            # Refresh is considered only AFTER the entire list produced no valid candidate.
            # No timer/cooldown: the button's visual FREE state is the source of truth.
            if refresh_available(frame):
                do_refresh(frame)
            else:
                # V44: tokens exist, but there is no allowed opponent and Refresh
                # currently costs gems. Nothing useful can change immediately, so
                # stay quiet and re-check once after 5 minutes instead of rescanning
                # the same list every few seconds.
                status("Nema dozvoljenog protivnika i Refresh nije FREE -> nova provera za 5 min.")
                for _ in range(300):
                    if stop_requested:
                        break
                    time.sleep(1)
    except Exception as e:
        status(f"GRESKA: {e}")
        running = False
        return
    finally:
        running = False
        if stop_requested:
            status("Zaustavljeno.")


# ================= SIMPLE GUI =================
def start_bot():
    global paused
    paused = False
    if not running:
        threading.Thread(target=worker, daemon=True).start()


def toggle_pause():
    global paused
    paused = not paused
    status("PAUZA" if paused else "Nastavljeno")


def stop_bot():
    global stop_requested
    stop_requested = True


def emergency_stop():
    stop_bot()


def gui():
    global status_callback,log_callback,power_callback,stats_callback,arena_stats_callback,POWER_LIMIT_K,SCAN_INTERVAL,BLACKLIST_ENABLED,AUTO_POWER_LIMIT
    root=tk.Tk(); root.title("Raid Arena Auto v57 Arena Only"); root.geometry("680x880"); root.resizable(False,False)

    # V41 dark Raid/fantasy background. Keep a reference so Tk does not GC it.
    try:
        root._bg_img=tk.PhotoImage(file=str(BASE/"app_background.png"))
        root._bg_label=tk.Label(root,image=root._bg_img,borderwidth=0)
        root._bg_label.place(x=0,y=0,relwidth=1,relheight=1)
        root._bg_label.lower()
    except Exception:
        root.configure(bg="#120d0c")

    style=ttk.Style(root)
    try: style.theme_use("clam")
    except Exception: pass
    style.configure("TFrame",background="#211614")
    style.configure("TLabelframe",background="#211614",foreground="#f2d6b3")
    style.configure("TLabelframe.Label",background="#211614",foreground="#e8a45d")
    style.configure("TLabel",background="#211614",foreground="#f3e7d7")
    style.configure("TCheckbutton",background="#211614",foreground="#f3e7d7")
    style.configure("TButton",padding=5,background="#4a2720",foreground="#ffe6c8")
    style.map("TButton",background=[("active","#6a3828")],foreground=[("active","#ffffff")])
    style.map("TCheckbutton",background=[("active","#2b1b18")],foreground=[("active","#ffc078")])

    lang=tk.StringVar(value="Srpski")
    state=tk.StringVar(value="Spremno."); powers=tk.StringVar(value="—")
    statsv=tk.StringVar(value="")
    limitv=tk.StringVar(value="500"); speedv=tk.StringVar(value="Fast")
    gemv=tk.BooleanVar(value=False); autolimitv=tk.BooleanVar(value=False)

    def sync_gem_permission(*_):
        global ALLOW_GEM_REFILL
        # Immediate live safety state: GUI checkbox is the source of truth.
        ALLOW_GEM_REFILL = (gemv.get() is True)

    gemv.trace_add("write", sync_gem_permission)
    sync_gem_permission()
    optimalv=tk.StringVar(value="")
    last_state={"stats":{"battles":0,"wins":0,"losses":0,"refreshes":0,"refills":0},
                "arena":arena_power_statistics(),"powers":[]}

    T={
      "sr":{
        "ready":"Spremno.","title":"Raid Classic Arena Auto","cal":"Kalibrisana veličina Raid prozora: {w} × {h} px",
        "warn":"Program upozorava ako je prozor značajno drugačiji.","lang":"Jezik:",
        "max":"Max Team Power (K):","speed":"Brzina:","auto":"Automatski Team Power limit po statistici",
        "gem":"Dozvoli GEM refill (40 gems)","black":"Blacklist heroji — ako je čekiran, taj enemy tim se ne napada",
        "start":"START","pause":"PAUZA","stop":"STOP","apply":"PRIMENI","size":"AUTO PODESI RAID PROZOR","frame":"PRIKAŽI OKVIR",
        "cands":"","actions":"Poslednje akcije",
        "footer":"F8 = emergency STOP | failsafe: miš u gornji levi ugao",
        "battles":"Bitke {battles} | Pobede {wins} | Porazi {losses} | Refresh {refreshes} | Refill {refills}",
        "none":"Nema kandidata < limita","opt0":"Optimalno po statistici: još nema zabeleženih borbi",
        "optwait":"Optimalno po statistici: čeka podatke ({samples}/15) | Pobede {wins} | Porazi {losses} | WR {wr:.1f}%",
        "opt":"Optimalno: {optimal:.0f}K | Pobede {wins} | Porazi {losses} | WR {wr:.1f}%",
        "resetstats":"RESET STATISTIKE","resetdone":"Arena statistika resetovana.","resetfail":"GREŠKA: statistika nije resetovana.","set":"Podešeno","yes":"DA","no":"NE","numerr":"GREŠKA: Max Team Power mora biti broj.",
        "sized":"Raid podešen: {w}x{h} @ ({l},{t})","err":"GREŠKA: {e}"
      },
      "en":{
        "ready":"Ready.","title":"Raid Classic Arena Auto","cal":"Calibrated Raid window size: {w} × {h} px",
        "warn":"The app warns you if the Raid window size is significantly different.","lang":"Language:",
        "max":"Max Team Power (K):","speed":"Speed:","auto":"Automatically choose Team Power limit from statistics",
        "gem":"Allow GEM refill (40 gems)","black":"Hero blacklist — if checked, teams containing that hero are skipped",
        "start":"START","pause":"PAUSE","stop":"STOP","apply":"APPLY","size":"AUTO SIZE RAID WINDOW","frame":"SHOW FRAME",
        "cands":"","actions":"Recent actions",
        "footer":"F8 = emergency STOP | failsafe: move mouse to top-left corner",
        "battles":"Battles {battles} | Wins {wins} | Losses {losses} | Refresh {refreshes} | Refill {refills}",
        "none":"No candidates below limit","opt0":"Statistical optimum: no recorded battles yet",
        "optwait":"Statistical optimum: collecting data ({samples}/15) | Wins {wins} | Losses {losses} | WR {wr:.1f}%",
        "opt":"Optimal: {optimal:.0f}K | Wins {wins} | Losses {losses} | WR {wr:.1f}%",
        "resetstats":"RESET STATS","resetdone":"Arena statistics reset.","resetfail":"ERROR: statistics were not reset.","set":"Applied","yes":"YES","no":"NO","numerr":"ERROR: Max Team Power must be a number.",
        "sized":"Raid window set: {w}x{h} @ ({l},{t})","err":"ERROR: {e}"
      }
    }
    def tr(k): return T["sr" if lang.get()=="Srpski" else "en"][k]

    blvars={name:tk.BooleanVar(value=BLACKLIST_ENABLED.get(name,True)) for name in ("Tekteon","Mavara","Sabrael","Hekaton","Solonar","TMNT")}

    top=ttk.Frame(root); top.pack(fill="x",padx=18,pady=(10,0))
    title_lbl=ttk.Label(top,font=("Segoe UI",16,"bold")); title_lbl.pack(side="left")
    lang_lbl=ttk.Label(top); lang_lbl.pack(side="right",padx=(5,3))
    langbox=ttk.Combobox(top,textvariable=lang,values=["Srpski","English"],state="readonly",width=9); langbox.pack(side="right")
    cal_lbl=ttk.Label(root,font=("Segoe UI",10,"bold")); cal_lbl.pack()
    warn_lbl=ttk.Label(root); warn_lbl.pack(pady=(0,8))

    cfg=ttk.Frame(root); cfg.pack(pady=5)
    max_lbl=ttk.Label(cfg); max_lbl.grid(row=0,column=0)
    ttk.Entry(cfg,textvariable=limitv,width=9).grid(row=0,column=1,padx=(5,20))
    speed_lbl=ttk.Label(cfg); speed_lbl.grid(row=0,column=2)
    ttk.Combobox(cfg,textvariable=speedv,values=["Fast","Normal"],state="readonly",width=9).grid(row=0,column=3,padx=5)
    auto_cb=ttk.Checkbutton(cfg,variable=autolimitv); auto_cb.grid(row=1,column=0,columnspan=4,pady=(7,0),sticky="w")
    statsrow=ttk.Frame(cfg); statsrow.grid(row=2,column=0,columnspan=4,pady=(3,2),sticky="ew")
    ttk.Label(statsrow,textvariable=optimalv).pack(side="left")
    reset_stats_btn=ttk.Button(statsrow,width=18)
    reset_stats_btn.pack(side="left",padx=(10,0))
    gem_cb=ttk.Checkbutton(cfg,variable=gemv); gem_cb.grid(row=3,column=0,columnspan=4,pady=(5,0),sticky="w")

    blf=ttk.LabelFrame(root)
    blf.pack(fill="x",padx=18,pady=(2,6))
    # V42: expose every supported blacklist group in the GUI.
    blacklist_ui=("Tekteon","Mavara","Sabrael","Hekaton","Solonar","TMNT")
    for i,name in enumerate(blacklist_ui):
        row=i//3
        col=i%3
        ttk.Checkbutton(blf,text=name,variable=blvars[name]).grid(
            row=row,column=col,padx=12,pady=5,sticky="w"
        )

    def set_live_arena_stats(ast):
        last_state["arena"]=ast
        if ast["samples"]==0: optimalv.set(tr("opt0"))
        elif ast["optimal"] is None:
            optimalv.set(tr("optwait").format(samples=ast["samples"],wins=ast["wins"],losses=ast["samples"]-ast["wins"],wr=(ast["winrate"] or 0)*100))
        else:
            optimalv.set(tr("opt").format(optimal=ast["optimal"],wins=ast["wins"],losses=ast["samples"]-ast["wins"],wr=(ast["winrate"] or 0)*100))

    def do_reset_stats():
        if reset_arena_statistics():
            ast=arena_power_statistics()
            set_live_arena_stats(ast)
            status(tr("resetdone"))
        else:
            status(tr("resetfail"))

    reset_stats_btn.configure(command=do_reset_stats)

    def apply():
        global POWER_LIMIT_K,SCAN_INTERVAL,ALLOW_GEM_REFILL,BLACKLIST_ENABLED,AUTO_POWER_LIMIT
        try:
            POWER_LIMIT_K=float(limitv.get()); SCAN_INTERVAL=.6 if speedv.get()=="Fast" else 1.2
            ALLOW_GEM_REFILL=bool(gemv.get()); BLACKLIST_ENABLED={n:bool(v.get()) for n,v in blvars.items()}
            AUTO_POWER_LIMIT=bool(autolimitv.get()); set_live_arena_stats(arena_power_statistics())
            status(f"{tr('set')}: <{POWER_LIMIT_K:g}K | {speedv.get()} | GEM refill: {tr('yes') if ALLOW_GEM_REFILL else tr('no')} | active limit: {effective_power_limit():g}K")
        except ValueError: status(tr("numerr"))

    bf=ttk.Frame(root); bf.pack(pady=9)
    start_btn=ttk.Button(bf,command=lambda:(apply(),start_bot()),width=13); start_btn.grid(row=0,column=0,padx=4)
    pause_btn=ttk.Button(bf,command=toggle_pause,width=13); pause_btn.grid(row=0,column=1,padx=4)
    stop_btn=ttk.Button(bf,command=stop_bot,width=13); stop_btn.grid(row=0,column=2,padx=4)
    apply_btn=ttk.Button(bf,command=apply,width=13); apply_btn.grid(row=0,column=3,padx=4)

    wf=ttk.Frame(root); wf.pack(pady=(0,7))
    def do_auto_size():
        try:
            l,t,w,h=auto_size_raid(); status(tr("sized").format(w=w,h=h,l=l,t=t))
        except Exception as e: status(tr("err").format(e=e))
    size_btn=ttk.Button(wf,command=do_auto_size,width=27); size_btn.grid(row=0,column=0,padx=5)
    frame_btn=ttk.Button(wf,command=lambda:show_calibration_frame(root),width=20); frame_btn.grid(row=0,column=1,padx=5)

    ttk.Separator(root).pack(fill="x",padx=18,pady=3)
    ttk.Label(root,textvariable=state,font=("Segoe UI",10,"bold"),wraplength=630).pack(pady=7)
    ttk.Label(root,textvariable=statsv).pack()
    pf=ttk.LabelFrame(root)  # v41: candidate panel intentionally hidden; internal scan remains
    lf=ttk.LabelFrame(root); lf.pack(fill="both",expand=True,padx=18,pady=5)
    box=tk.Text(lf,height=13,state="disabled",font=("Consolas",9),bg="#120e0d",fg="#f4e6d5",insertbackground="#f4b66d"); box.pack(fill="both",expand=True,padx=5,pady=5)
    footer_lbl=ttk.Label(root); footer_lbl.pack(pady=(3,9))

    def refresh_language(*_):
        title_lbl.config(text=tr("title")); lang_lbl.config(text=tr("lang"))
        cal_lbl.config(text=tr("cal").format(w=CALIBRATED_W,h=CALIBRATED_H)); warn_lbl.config(text=tr("warn"))
        max_lbl.config(text=tr("max")); speed_lbl.config(text=tr("speed")); auto_cb.config(text=tr("auto")); gem_cb.config(text=tr("gem"))
        blf.config(text=tr("black")); reset_stats_btn.config(text=tr("resetstats"))
        start_btn.config(text=tr("start")); pause_btn.config(text=tr("pause"))
        stop_btn.config(text=tr("stop")); apply_btn.config(text=tr("apply")); size_btn.config(text=tr("size")); frame_btn.config(text=tr("frame"))
        pf.config(text=tr("cands")); lf.config(text=tr("actions")); footer_lbl.config(text=tr("footer"))
        st=last_state["stats"]; statsv.set(tr("battles").format(**st))
        vals=last_state["powers"]; powers.set(" | ".join(f"{v:g}K" for v in vals) if vals else tr("none"))
        set_live_arena_stats(last_state["arena"])
        if state.get() in ("Spremno.","Ready."): state.set(tr("ready"))

    status_callback=lambda m: root.after(0,state.set,str(m))
    def addlog(m):
        def f():
            box.configure(state="normal"); box.insert("end",time.strftime("%H:%M:%S")+"  "+str(m)+"\n")
            lines=int(box.index("end-1c").split(".")[0])
            if lines>22: box.delete("1.0",f"{lines-20}.0")
            box.see("end"); box.configure(state="disabled")
        root.after(0,f)
    log_callback=addlog
    def setpowers(vals):
        last_state["powers"]=list(vals)
        powers.set(" | ".join(f"{v:g}K" for v in vals) if vals else tr("none"))
    power_callback=lambda vals: root.after(0,setpowers,vals)
    def setstats(st):
        last_state["stats"]=dict(st); statsv.set(tr("battles").format(**st))
    stats_callback=lambda st: root.after(0,setstats,st)
    arena_stats_callback=lambda ast: root.after(0,set_live_arena_stats,ast)

    langbox.bind("<<ComboboxSelected>>",refresh_language)
    refresh_language()
    try:
        import keyboard; keyboard.add_hotkey("f8",emergency_stop)
    except Exception: pass
    root.mainloop()


if __name__ == "__main__":
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.08
    gui()
