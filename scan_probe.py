# -*- coding: utf-8 -*-
"""مِسبارُ دفعة السكانر — يقيس ولا يغيّر سلوكاً.

[مُضاف 2026-09-12] — للبند الوحيد الباقي مفتوحاً في تسليم 2026-09-11:
**دفعةٌ استغرقت 49.6 دقيقة** بينما القياسُ على الدالّة أعطى 11.6 ث/عملة.
والتسليمُ نفسُه يقول: «المشتبَهُ بهما — ولم أقِس أيّاً منهما».

هذا المِسبار يقيسهما. لا يُقدِّر ولا يستنتج: يَزِن.

## ماذا يفصل

    زمنُ الدفعة الكلّيّ
      ├─ مجموعُ _scan_one                 ← المشتبَهُ الأوّل
      │    ├─ انتظارُ fetch_ohlcv          ← شبكةٌ ومنظّمُ معدّل
      │    └─ الباقي                       ← pandas · talib · منطق
      ├─ check_position_reversals         ← المشتبَهُ الثاني
      └─ الباقي                            ← ما بين الدالّتين

وكذلك **نداءاتُ الشبكة من خيوطٍ أخرى أثناء الدفعة** — لأن كائن
`exchange` واحدٌ مشترَك، ومنظّمُ معدّل ccxt **يُسلسِل** كلَّ من يمسّه:
السكانر، ومراقبُ OI، وشريطُ الحيتان، و**كلُّ طلبِ لوحةٍ** تفتحها.
فدفعةٌ تُقاس واللوحةُ مفتوحة ليست دفعةً تُقاس وهي مغلقة.

## التركيب — سطرٌ واحد

في `quantum_matrix_v2.py`، داخل `main()` بعد إطلاق الخيوط مباشرةً:

    import scan_probe; scan_probe.install(sys.modules[__name__])

والإزالة: احذف السطر. المِسبارُ لا يترك أثراً في أيّ ملفّ بيانات.

## القراءة

يُكتَب سطرُ «📊 مِسبار» في `quantum_matrix_v2.log` عند نهاية **كلّ**
دفعة. والدفعةُ الأولى بعد الإقلاع تُوسَم `[باردة]` — فرضيّةُ «الخبائثُ
باردة» تُحسَم بمقارنتها بالثانية، لا بالتقدير. ومع كلّ سطرٍ رقمُ دفعةٍ
(`#1` · `#2` …) حتّى تُقارَن دفعتان بعينهما لا دفعتان مظنونتان.

## ما صُلِّح في مراجعة 2026-09-12 — وكلُّه مقيسٌ بفحصٍ في `tests/`

    ① الانحدارُ اللانهائيّ عند إعادة التركيب   ⇒ الأصولُ في الإغلاق + وسمٌ
    ② سكانرٌ متعدّدُ الخيوط يَكسِر الإسناد       ⇒ مجموعةُ خيوطٍ + حالةٌ خيطيّة
    ③ دفعةٌ بلا نداءِ انعكاسٍ تتراكم بلا حدّ     ⇒ سقفٌ وتحذيرٌ صريح
    ④ نِسَبٌ فوق 100% وفجوةٌ سالبة              ⇒ زمنُ جدارٍ باتّحاد المُدد

🛑 قراءةٌ محضة. لا يُصدر أمراً ولا يكتب ملفّاً ولا يمسّ السوق.
🛑 **ولا يُسقِط البوتَ**: `install` لا يرفع استثناءً افتراضيّاً — يُعلِن
   سببَ تعذّره ويُرجع False، لأنّ مِسباراً يقتل العمليّةَ أسوأُ من مِسبارٍ
   لا يعمل. ومَن أراد الصرامةَ فـ`install(mod, strict=True)`.
"""
import functools
import threading
import time

__all__ = ["install", "uninstall", "installed"]

#: وسمٌ يُوضَع على كلّ دالّةٍ ملفوفة — به يُكتشَف ترقيعٌ سابقٌ فُقدت أصولُه.
_MARK = "__scan_probe_wrapper__"

#: المشتبَهُ الثالث — يُلَفّ إن وُجد على الوحدة أو على الصنف.
_CARD = "_mtf_card_fields"

#: عتبةُ التحذير **قبل** أن تكتمل دفعةٌ واحدة — فحجمُ الدفعة مجهولٌ
#: حينها. وسجلُّ المستخدم يقول «فُحصت دفعة 40 عملة (40/100)» ⇒ الكونُ
#: مئة، فلا دفعةَ تتجاوزها. وثلاثُ مئةٍ = ثلاثُ دفعاتٍ عند سقف الكون،
#: فلا تُطلَق داخل دفعةٍ مشروعةٍ ولو مسحت الكونَ كلَّه.
#: وبعد أوّل دفعةٍ مكتملة **تُعاير العتبةُ نفسَها** ⇒ ‎_warn_at()‎.
_WARN_COINS = 300

#: حدٌّ أدنى للعتبة المعايَرة — حتى لا تُطلَق على دفعةٍ صغيرةٍ شاذّة.
_WARN_FLOOR = 60

#: سقفُ العملات المخزَّنة في دفعةٍ واحدة. يُتجاوَز فقط إن لم يُنادَ
#: `check_position_reversals` — وحينها الحدودُ مشكوكٌ فيها أصلاً.
_MAX_COINS = 500

_LOCK = threading.Lock()
_ORIG = {}
_S = {}
_TL = threading.local()          # العملةُ الجارية لكلّ خيطٍ على حدة


def _reset(cold=False, seq=0):
    _S.clear()
    _S.update(t0=None, cold=cold, seq=seq, coins=[], spans=[], scan_s=0.0,
              fetch_n=0, fetch_s=0.0, other_n=0, other_s=0.0,
              threads=set(), in_cpr=False, cpr_thread=None, dropped=0,
              card_n=0, card_s=0.0, card_in_n=0, card_in_s=0.0,
              card_gap_n=0, card_gap_s=0.0, card_oth_n=0, card_oth_s=0.0,
              card_out_n=0, card_out_s=0.0,
              tf_n={}, tf_dup={}, tf_und=0, seen={}, warned=False)


_reset(cold=True)


def _fmt(sec):
    return "%.1f د" % (sec / 60.0) if sec >= 90 else "%.1f ث" % sec


def _union(spans):
    """زمنُ الجدار الذي شُغل بالمسح فعلاً — اتّحادُ المُدد لا مجموعُها.

    بخيطٍ واحدٍ يساوي المجموع. وبخيوطٍ متوازيةٍ يساوي الزمنَ المنقضي
    حقّاً، فلا تظهر نسبةٌ فوق 100% ولا فجوةٌ سالبة.
    """
    if not spans:
        return 0.0
    ordered = sorted(spans)
    total = 0.0
    cs, ce = ordered[0]
    for s, e in ordered[1:]:
        if s > ce:
            total += ce - cs
            cs, ce = s, e
        elif e > ce:
            ce = e
    return total + (ce - cs)


def _warn_at():
    """العتبةُ السارية: ما طلبه المستخدم · أو ثلاثةُ أضعافِ أوّلِ دفعةٍ
    مكتملة · أو _WARN_COINS قبل أن تكتمل واحدة."""
    want = _ORIG.get("warn_coins")
    if want:
        return want
    n = _ORIG.get("batch_n")
    if n:
        return max(3 * n, _WARN_FLOOR)
    return _WARN_COINS


def _ohlcv_args(a, k):
    """(رمز · إطار · حدّ) من وسائط ccxt: fetch_ohlcv(symbol, timeframe,
    since, limit, params). يعيد أصفاراً صامتةً إن تغيّرت البصمة."""
    try:
        sym = a[0] if len(a) > 0 else k.get("symbol")
        tf = a[1] if len(a) > 1 else k.get("timeframe")
        lim = a[3] if len(a) > 3 else k.get("limit")
        if not isinstance(lim, int):
            lim = None
        return sym, tf, lim
    except Exception:                                        # noqa: BLE001
        return None, None, None


def installed():
    """هل المِسبارُ مُركَّبٌ الآن."""
    return bool(_ORIG)


def install(mod, log=None, strict=False, warn_coins=None):
    """يُركّب المِسبار على وحدةِ البوت. يعيد True إن رُكِّب.

    ولا يرفع استثناءً افتراضيّاً: يُعلِن السببَ ويُرجع False — إلّا مع
    `strict=True`.

    و`warn_coins` يثبّت عتبةَ حارسِ «نهاية الدفعة» بدل معايرتها ذاتيّاً.
    """
    if log is None:
        def log(m):
            try:
                mod.STATE.log_activity(m)
            except Exception:                                # noqa: BLE001
                print(m)

    def fail(why):
        if strict:
            raise RuntimeError("scan_probe: " + why)
        log("📊 مِسبار الدفعة: لم يُركَّب — %s" % why)
        return False

    if _ORIG:
        log("📊 مِسبار الدفعة: مُركَّبٌ سابقاً — لا يُركَّب مرّتين.")
        return False

    ex = getattr(mod, "exchange", None)
    cls = getattr(mod, "TechnicalScanner", None)
    cpr = getattr(mod, "check_position_reversals", None)
    if ex is None or cls is None or cpr is None:
        return fail("لم أجد exchange/TechnicalScanner/"
                    "check_position_reversals في الوحدة الممرَّرة")

    orig_fetch = getattr(ex, "fetch_ohlcv", None)
    orig_scan = getattr(cls, "_scan_one", None)
    if orig_fetch is None or orig_scan is None:
        return fail("الكائنُ أو الصنفُ بلا fetch_ohlcv/_scan_one")

    # ── ⓪ ترقيعٌ سابقٌ فُقدت أصولُه؟ التركيبُ فوقه انحدارٌ لانهائيّ ──────
    stale = [nm for nm, target in (("exchange.fetch_ohlcv", orig_fetch),
                                   ("TechnicalScanner._scan_one", orig_scan),
                                   ("check_position_reversals", cpr))
             if getattr(target, _MARK, False)]
    if stale:
        return fail("ترقيعُ مِسبارٍ سابقٍ ما زال على %s وأصولُه مفقودة "
                    "(إعادةُ تحميلِ الوحدة مثلاً). التركيبُ فوقه يُنتج "
                    "انحداراً لانهائيّاً — أعد تشغيل العمليّة أوّلاً."
                    % " · ".join(stale))

    # هل الصفةُ مملوكةٌ للكائن/الصنف أم موروثة — لتعودَ الإزالةُ نظيفةً
    _ORIG["ex"] = ex
    _ORIG["cls"] = cls
    _ORIG["warn_coins"] = warn_coins
    _ORIG["batch_n"] = None
    _ORIG["fetch_ohlcv"] = orig_fetch
    _ORIG["_scan_one"] = orig_scan
    _ORIG["cpr"] = cpr
    _ORIG["ex_own"] = "fetch_ohlcv" in vars(ex)
    _ORIG["cls_own"] = "_scan_one" in vars(cls)

    # ── ① كلُّ نداءِ شموع — ومن أيّ خيط ─────────────────────────────
    @functools.wraps(orig_fetch)
    def fetch_ohlcv(*a, **k):
        t = time.perf_counter()
        try:
            return orig_fetch(*a, **k)
        finally:
            d = time.perf_counter() - t
            ident = threading.get_ident()
            with _LOCK:
                if _S["t0"] is None:
                    pass          # لا دفعةَ جارية — لا يُحسَب على أحد
                elif _S["in_cpr"] and ident == _S["cpr_thread"]:
                    pass          # تُحسَب ضمن rev_s وحدَه — لا تُزدوَج
                elif ident in _S["threads"]:
                    _S["fetch_n"] += 1
                    _S["fetch_s"] += d
                    cur = getattr(_TL, "cur", None)
                    if cur is not None:
                        cur[1] += 1
                        cur[2] += d
                    # فائضُ § ٢‑أ مقيساً لا مَعدوداً على الشجرة: نداءٌ
                    # يُقتطَع من خبيئةٍ مفتاحُها (رمز·إطار) إن سبقَه نداءٌ
                    # لنفسهما بحدٍّ لا يقلّ عن حدِّه.
                    sym, tf, lim = _ohlcv_args(a, k)
                    if tf is not None:
                        _S["tf_n"][tf] = _S["tf_n"].get(tf, 0) + 1
                        key = (sym, tf)
                        prev = _S["seen"].get(key, "ــ")
                        if prev == "ــ":
                            _S["seen"][key] = lim
                        elif lim is None or prev is None:
                            _S["tf_und"] += 1       # حدٌّ مجهول ⇒ لا يُبَتّ
                        elif prev >= lim:
                            _S["tf_dup"][tf] = _S["tf_dup"].get(tf, 0) + 1
                        else:
                            _S["seen"][key] = lim
                else:
                    _S["other_n"] += 1
                    _S["other_s"] += d
    setattr(fetch_ohlcv, _MARK, True)
    ex.fetch_ohlcv = fetch_ohlcv

    # ── ② كلُّ عملةٍ على حدة — وبخيوطٍ متوازيةٍ بلا خلط ───────────────
    @functools.wraps(orig_scan)
    def _scan_one(self, ticker, *a, **k):
        ident = threading.get_ident()
        t = time.perf_counter()
        cur = [ticker, 0, 0.0, 0.0]
        with _LOCK:
            if _S["t0"] is None:
                _S["t0"] = t
            _S["threads"].add(ident)       # كلُّ خيطٍ يمسح هو خيطُ سكانر
        prev = getattr(_TL, "cur", None)
        _TL.cur = cur
        try:
            return orig_scan(self, ticker, *a, **k)
        finally:
            e = time.perf_counter()
            cur[3] = e - t
            _TL.cur = prev
            warn = False
            with _LOCK:
                _S["scan_s"] += cur[3]
                if len(_S["coins"]) < _MAX_COINS:
                    _S["coins"].append(cur)
                    _S["spans"].append((t, e))
                else:
                    _S["dropped"] += 1
                at = _warn_at()
                if len(_S["coins"]) >= at and not _S["warned"]:
                    _S["warned"] = warn = True
            if warn:
                b_n = _ORIG.get("batch_n")
                log("📊 ⚠ مِسبار الدفعة: %d عملةً مُسحت ولم يُرَ نداءُ "
                    "check_position_reversals بعد (العتبة %d — %s). فإمّا "
                    "أنّه يُنادى بمرجعٍ مستورَدٍ لا يراه الترقيع، وإمّا أنّ "
                    "الدورة تُقطَع قبله ⇒ **تعريفُ «نهاية الدفعة» لا يصحّ "
                    "هنا، ولا سطرَ قياسٍ يُبنى عليه.**"
                    % (at, at,
                       "طلبُك" if _ORIG.get("warn_coins") else
                       ("ثلاثةُ أضعافِ دفعةٍ مقيسةٍ بـ%d عملة" % b_n) if b_n
                       else "قبل أيّ دفعةٍ مكتملة — ثلاثُ دفعاتٍ عند سقف "
                            "الكون 100"))
    setattr(_scan_one, _MARK, True)
    cls._scan_one = _scan_one

    # ── ③ ونهايةُ الدفعة — تُعرَف بأنّها لحظةُ استدعاء المشتبَه الثاني ──
    @functools.wraps(cpr)
    def check_position_reversals(*a, **k):
        ident = threading.get_ident()
        with _LOCK:
            _S["in_cpr"] = True
            _S["cpr_thread"] = ident
        t = time.perf_counter()
        try:
            return cpr(*a, **k)
        finally:
            rev = time.perf_counter() - t
            with _LOCK:
                _S["in_cpr"] = False
                _S["cpr_thread"] = None
            try:
                _emit(rev, log)
            except Exception as e:                           # noqa: BLE001
                log("📊 مِسبار: تعذّر الإخراج — %s" % type(e).__name__)
    setattr(check_position_reversals, _MARK, True)
    mod.check_position_reversals = check_position_reversals
    _ORIG["mod"] = mod

    # ── ④ المشتبَهُ الثالث — بطاقةُ MTF، إن وُجدت ─────────────────────
    # تسليمُ 2026-09-11 § د يقيسها 11.6 ث/نداء، و§ ٣‑أ يصحّح مُضاعِفَها:
    # لكلّ **مرشَّح** (35) وكلّ **مؤجَّلٍ ناضج** (21) = 56 ⇒ ~10.8 دقيقة.
    # وبلا لفِّها تسقط كلُّها في سطر «ما بين الدالّتين» بلا اسم.
    card, card_on = getattr(mod, _CARD, None), "mod"
    if card is None:
        card, card_on = getattr(cls, _CARD, None), "cls"
    if card is None or getattr(card, _MARK, False):
        _ORIG["card_on"] = None
        log("📊 مِسبار الدفعة: لم أجد %s — فزمنُها (إن وُجدت) يبقى داخل "
            "سطر «ما بين الدالّتين» بلا اسم." % _CARD)
    else:
        owner = mod if card_on == "mod" else cls
        _ORIG["card_on"] = card_on
        _ORIG["card"] = card
        _ORIG["card_own"] = _CARD in vars(owner)

        @functools.wraps(card)
        def _mtf_card_fields(*a, **k):
            t = time.perf_counter()
            try:
                return card(*a, **k)
            finally:
                d = time.perf_counter() - t
                ident = threading.get_ident()
                in_scan = getattr(_TL, "cur", None) is not None
                with _LOCK:
                    _S["card_n"] += 1
                    _S["card_s"] += d
                    if _S["t0"] is None:
                        _S["card_out_n"] += 1      # لا دفعةَ جارية
                        _S["card_out_s"] += d
                    elif ident not in _S["threads"]:
                        _S["card_oth_n"] += 1      # خيطُ لوحةٍ أو مراقب
                        _S["card_oth_s"] += d
                    elif in_scan:
                        _S["card_in_n"] += 1       # داخل _scan_one
                        _S["card_in_s"] += d
                    else:
                        _S["card_gap_n"] += 1      # ما بين الدالّتين
                        _S["card_gap_s"] += d
        setattr(_mtf_card_fields, _MARK, True)
        setattr(owner, _CARD, _mtf_card_fields)

    log("📊 مِسبار الدفعة: رُكِّب — سطرُ قياسٍ عند نهاية كلّ دفعة. "
        "قراءةٌ محضة، والإزالةُ بحذف سطر التركيب.")
    return True


def _emit(rev_s, log):
    with _LOCK:
        if _S["t0"] is None or not _S["coins"]:
            return
        total = time.perf_counter() - _S["t0"]
        coins = list(_S["coins"])
        spans = list(_S["spans"])
        scan_s, fetch_n, fetch_s = _S["scan_s"], _S["fetch_n"], _S["fetch_s"]
        other_n, other_s = _S["other_n"], _S["other_s"]
        cold, seq, dropped = _S["cold"], _S["seq"], _S["dropped"]
        n_threads = len(_S["threads"])
        tf_n = dict(_S["tf_n"])
        tf_dup = dict(_S["tf_dup"])
        tf_und = _S["tf_und"]
        card = (_S["card_n"], _S["card_s"], _S["card_in_n"], _S["card_in_s"],
                _S["card_gap_n"], _S["card_gap_s"],
                _S["card_oth_n"], _S["card_oth_s"],
                _S["card_out_n"], _S["card_out_s"])
        _reset(cold=False, seq=seq + 1)

    n = len(coins)
    if not dropped:                       # دفعةٌ سليمةٌ ⇒ يُعايَر عليها
        _ORIG["batch_n"] = n
    wall = _union(spans)                  # زمنُ الجدار المشغولُ بالمسح
    cpu = scan_s - fetch_s                # داخل _scan_one وليس شبكة
    gap = total - wall - rev_s            # بين الدالّتين
    par = (scan_s / wall) if wall > 0 else 1.0
    # كم عملةً خرجت مبكّراً — يُستدلّ بعددِ نداءاتها لا بالتخمين
    early = sum(1 for c in coins if c[1] <= 2)
    full = sum(1 for c in coins if c[1] >= 10)
    slow = sorted(coins, key=lambda c: -c[3])[:5]

    log("📊 مِسبار الدفعة #%d%s: %s إجمالاً · %d عملة · خيوطُ مسحٍ %d"
        % (seq + 1, " **[باردة — الأولى بعد الإقلاع]**" if cold else "",
           _fmt(total), n, n_threads))
    # الجدارُ (اتّحادُ المُدد) ومجاميعُ الخيوط كمّيّتان مختلفتان — ولا
    # يُجمَعان في معادلةٍ واحدة، فبخيوطٍ متوازيةٍ يصير «جدار = شبكة + حساب»
    # كذباً حسابيّاً. فالجدارُ سطرٌ، وتفكيكُ المجموع سطرٌ يقول إنّه مجموع.
    log("📊   _scan_one جداراً %s (%.0f%% من الدفعة)%s"
        % (_fmt(wall), 100.0 * wall / total if total else 0,
           "" if par < 1.05 else
           " · مجموعُ الخيوط %s ⇒ تواشٍ ×%.1f" % (_fmt(scan_s), par)))
    log("📊        ومنه شبكة %s (%d نداء) + حساب %s%s"
        % (_fmt(fetch_s), fetch_n, _fmt(cpu),
           "" if par < 1.05 else "  — مجاميعُ خيوطٍ لا زمنَ جدار"))
    log("📊   check_position_reversals %s · وما بين الدالّتين %s"
        % (_fmt(rev_s), _fmt(gap)))
    log("📊   وخيوطٌ أخرى مسّت نفسَ الكائن أثناء الدفعة: %d نداءً · %s "
        "⇒ سلسلةٌ على منظّم المعدّل%s"
        % (other_n, _fmt(other_s),
           "" if n_threads < 2 else " (خيوطُ السكانر ليست منها)"))
    log("📊   خرجت مبكّراً (≤2 نداء) %d · مسحاً كاملاً (≥10) %d · "
        "الوسيط %.1f ث/عملة"
        % (early, full, sorted(c[3] for c in coins)[n // 2]))
    log("📊   أبطأُ خمس: " + " · ".join(
        "%s %.1f ث/%d نداء" % (c[0], c[3], c[1]) for c in slow))
    if tf_n:
        calls = sum(tf_n.values())
        dup = sum(tf_dup.values())
        log("📊   الأطر: %d نداءً · %d إطاراً متمايزاً · مُقتطَعٌ بخبيئة "
            "(رمز·إطار) %d (%.0f%%)%s"
            % (calls, len(tf_n), dup, 100.0 * dup / calls if calls else 0,
               "" if not tf_und else " · غيرُ مبتوتٍ %d (حدٌّ مجهول)" % tf_und))
        log("📊        " + " · ".join(
            "%s %d (فائض %d)" % (tf, n, tf_dup.get(tf, 0))
            for tf, n in sorted(tf_n.items(), key=lambda x: -x[1])[:6]))
    if _ORIG.get("card_on"):
        c_n, c_s, i_n, i_s, g_n, g_s, o_n, o_s, u_n, u_s = card
        if c_n:
            log("📊   %s %d نداءً · %s = داخل المسح %d (%s) + بين الدالّتين "
                "%d (%s) + خيوطٌ أخرى %d (%s) + خارج الدفعة %d (%s)"
                % (_CARD, c_n, _fmt(c_s), i_n, _fmt(i_s), g_n, _fmt(g_s),
                   o_n, _fmt(o_s), u_n, _fmt(u_s)))
        else:
            log("📊   %s: صفرُ نداءاتٍ في هذه الدفعة" % _CARD)
    if dropped:
        log("📊 ⚠ تجاوزت الدفعةُ %d عملة ولم يُنادَ check_position_reversals: "
            "أُسقطت %d عملة ⇒ هذا السطرُ يجمع دفعاتٍ لا دفعةً، ولا يُبنى "
            "عليه." % (_MAX_COINS, dropped))
    if gap < -0.05:
        log("📊 ⚠ فجوةٌ سالبة ⇒ نداءُ الانعكاس تداخلَ مع المسح، "
            "فالحدودُ ليست متعاقبة.")


def uninstall(mod=None):
    """يُزيل المِسبار ويعيد الدوالَّ الثلاث كما كانت. يعيد True إن أُزيل."""
    if not _ORIG:
        return False
    ex, cls = _ORIG["ex"], _ORIG["cls"]
    if _ORIG["ex_own"]:
        ex.fetch_ohlcv = _ORIG["fetch_ohlcv"]
    else:
        try:
            del ex.fetch_ohlcv           # لا تُخلَّف صفةٌ على الكائن
        except AttributeError:           # pragma: no cover
            ex.fetch_ohlcv = _ORIG["fetch_ohlcv"]
    if _ORIG["cls_own"]:
        cls._scan_one = _ORIG["_scan_one"]
    else:
        try:
            del cls._scan_one
        except AttributeError:           # pragma: no cover
            cls._scan_one = _ORIG["_scan_one"]
    if _ORIG.get("card_on"):
        owner = _ORIG["mod"] if _ORIG["card_on"] == "mod" else cls
        if _ORIG["card_own"]:
            setattr(owner, _CARD, _ORIG["card"])
        else:
            try:
                delattr(owner, _CARD)
            except AttributeError:           # pragma: no cover
                setattr(owner, _CARD, _ORIG["card"])
    target = mod if mod is not None else _ORIG["mod"]
    target.check_position_reversals = _ORIG["cpr"]
    _ORIG.clear()
    _reset(cold=True)
    return True
