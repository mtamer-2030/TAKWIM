# مخطّط مِحَكّ — المرجع الكامل للإخراج

## القيم المسموحة (enums)

- **kind**: `diagnostic` (تشخيصي) · `exercise` (تمرين) · `exam` (فرض)
- **level**: `TC` (الجذع المشترك) · `1BAC` (الأولى باك) · `2BAC` (الثانية باك)
- **competency** (الكفايات الخمس):
  - `problematization` = الأشكلة
  - `conceptualization` = المفهمة
  - `argumentation` = الحجاج
  - `synthesis` = التركيب
  - `knowledge` = الاستحضار
- **type**:
  - مقفلة (تصحيح يقيني): `mcq_single` · `mcq_multi` · `classify` · `order`
  - مفتوحة: `short_text` · `grid` · `long_text`
- **check_rule.type**: `any_of` · `all_of` · `none_of` · `min_chars` · `max_chars` · `regex`

## رموز الأخطاء المشخِّصة الأوّليّة (قابلة للتوسيع)

`copy_verbatim` نسخ من النصّ بلا تحويل · `argument_as_thesis` خلط الحجّة بالأطروحة ·
`example_as_argument` خلط المثال بالحجّة · `external_projection` إسقاط رأي خارج النصّ ·
`descriptive_not_problematic` سؤال معرفي لا إشكال · `statement_not_question` تقرير في
موضع الإشكال · `concept_confusion` خلط مفهومين · `missing_tension` غياب التوتّر.

## بنية JSON

```json
{
  "title": "…",
  "kind": "exercise",
  "level": "1BAC",
  "unit": "الوضع البشري",
  "concept": "الوعي واللاوعي",
  "stimuli": [{"id": "s1", "text": "نصّ الانطلاق…"}],
  "questions": [ … ]
}
```

### أنواع الأسئلة (payload)

```json
// mcq_single — جواب واحد. correct مؤشّر داخل المدى. diagnostics يربط البدائل الخاطئة برموز.
{"type":"mcq_single","competency":"conceptualization","stimulus":"s1",
 "prompt":"…","max_score":2,
 "payload":{"options":["…","…","…","…"],"correct":3,
            "diagnostics":{"0":"copy_verbatim","1":"argument_as_thesis"}}}

// mcq_multi — عدّة أجوبة (تنقيط جزئي آلي)
{"type":"mcq_multi","competency":"argumentation","prompt":"…","max_score":4,
 "payload":{"options":["…","…"],"correct":[0,3]}}

// classify — يقوم مقام «صل بسهم». category مؤشّر داخل مدى categories.
{"type":"classify","competency":"problematization","prompt":"…","max_score":3,
 "payload":{"categories":["إشكال فلسفي","سؤال معرفي","تكرار"],
            "items":[{"text":"…","category":0},{"text":"…","category":2}]}}

// order — correct_order ترتيب كامل لمؤشّرات items
{"type":"order","competency":"argumentation","prompt":"…","max_score":2,
 "payload":{"items":["…","…","…"],"correct_order":[2,0,1],"partial_credit":true}}

// short_text — بمؤشّرات؛ مجموع نقاط المؤشّرات = max_score
{"type":"short_text","competency":"problematization","prompt":"…","max_score":3,
 "payload":{"max_chars":250},
 "indicators":[
   {"id":"i1","text":"صاغ الإشكال استفهاماً","points":1,
    "check_rule":{"type":"any_of","patterns":["؟","هل ","ما الذي"]}},
   {"id":"i2","text":"استحضر موقفاً بالاسم","points":1,
    "check_rule":{"type":"any_of","patterns":["فرويد","ديكارت","سارتر"]}},
   {"id":"i3","text":"أبرز التوتّر بين موقفين","points":1}],
 "penalties":[{"text":"أعاد صياغة النصّ حرفياً","points":-1}]}

// long_text — فقرة إنشائية (للفروض)
{"type":"long_text","competency":"synthesis","prompt":"…","max_score":4,
 "payload":{"max_chars":1200,"scaffold":["مقدمة","عرض الموقفين","تركيب"]},
 "indicators":[{"id":"i1","text":"…","points":2}, …]}

// grid — جدول، مؤشّرات لكلّ خلية
{"type":"grid","competency":"argumentation","prompt":"…","max_score":3,
 "payload":{"columns":["الآلية الحجاجية","وظيفتها"],"rows":3,"max_chars_per_cell":150},
 "indicators":[…]}
```

## قواعد الرفض (يجب أن يمرّ إخراجك منها)

1. `correct` خارج مدى الخيارات ← مرفوض.
2. مجموع نقاط `indicators` لا يساوي `max_score` (في المفتوحة) ← مرفوض.
3. `competency` أو `type` أو `kind` أو `level` خارج القيم المسموحة ← مرفوض.
4. بديل مشخِّص بلا رمز خطأ (قيمة فارغة في `diagnostics`) ← مرفوض.
5. `mcq_single` بلا جواب صحيح واحد بالضبط ← مرفوض.
6. `classify.items[].category` أو `order.correct_order` خارج المدى/غير تامّ ← مرفوض.
7. `stimulus` يشير إلى `id` غير معرّف في `stimuli` ← مرفوض.

## صيغة قالب Word (إن اخترت الإخراج بقالب بدل JSON)

فقرات عادية. أسطر تبدأ بـ `#` إرشاد يُتجاهَل. النجمة `*` = الجواب الصحيح.

```
العنوان: …
النوع: تمرين            (تمرين|تشخيص|فرض)
المستوى: 1BAC           (TC|1BAC|2BAC)
المجزوءة: …
المفهوم: …
نص s1: نصّ الانطلاق…

س) اختيار | الكفاية=المفهمة | النقطة=2 | نص=s1
ما الأطروحة؟
- بديل خاطئ | خطأ=copy_verbatim
* البديل الصحيح

س) تصنيف | الكفاية=الأشكلة | النقطة=3
صنّف:
فئات: إشكال فلسفي ؛ سؤال معرفي ؛ تكرار
عنصر: النص الأول => إشكال فلسفي
عنصر: النص الثاني => سؤال معرفي

س) ترتيب | الكفاية=الحجاج | النقطة=2 | جزئي=نعم
رتّب (بالترتيب الصحيح):
1. الخطوة الأولى
2. الخطوة الثانية

س) قصير | الكفاية=الأشكلة | النقطة=3 | حد=250
صغ الإشكال.
مؤشر: صيغة استفهامية | 1 | قاعدة=يحتوي: ؟ ؛ هل
مؤشر: توتّر بين موقفين | 2
خصم: أعاد صياغة النصّ حرفياً | 1
```

أنواع القالب: `اختيار` `متعدد` `تصنيف` `ترتيب` `قصير` `مقال` `جدول`.
قواعد الفحص: `يحتوي` `يحتوي_الكل` `لا_يحتوي` `حد_أدنى: عدد` `حد_أقصى: عدد` `نمط: regex`.
