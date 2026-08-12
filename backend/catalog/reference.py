"""A realistic Rwandan pharmacy catalogue, shared by the seed commands.

Two things here are domain rules rather than sample data, and both were
wrong before this file existed.

**Packaging follows the dosage form, not a global constant.** A tablet
comes carton → pack → blister → tablet. A syrup has no blister; it has a
bottle. A vial has no blister either. Applying one chain to everything
produced "Blister of 10" on surgical gloves and on insulin, which is not
a thing that exists, and it made every quantity on screen meaningless.

**Sellability follows the licence.** A retail pharmacy does not sell a
carton of 1,200 capsules over the counter, and a wholesale pharmacy does
not break a pack to sell a single tablet. The same physical packaging is
therefore sellable in one organization and not in the other, which is
exactly what `UnitOfMeasure.is_sellable` is for — it is per catalogue
row, and catalogues are tenant-scoped.

Registration numbers are real-shaped Rwanda FDA identifiers. They matter
beyond decoration: `catalog.services.mirror_product` matches across
organizations on registration number, then GTIN, and never on name. A
product seeded without either forks into a duplicate the first time it
is received from a supplier.
"""

from __future__ import annotations

from dataclasses import dataclass

from catalog.models import LegalStatus, ProductTypeCode, TaxTreatment


@dataclass(frozen=True)
class Level:
    """One rung of a packaging chain."""

    code: str
    name: str
    factor: int
    #: Over the counter. False for anything a patient never buys whole.
    retail: bool
    #: To another pharmacy. False for anything a wholesaler will not break.
    wholesale: bool


# The atomic unit is always factor 1 and is always the base.
#
# Retail may sell down to the single tablet — partial-pack dispensing is
# the normal case in Rwanda, not an edge case. Retail may not sell a
# carton. Wholesale is the mirror: cartons and packs yes, loose units no.
TABLET = [
    Level("CARTON", "Carton of 12 packs", 1200, retail=False, wholesale=True),
    Level("PACK", "Pack of 100 tablets", 100, retail=True, wholesale=True),
    Level("BLISTER", "Blister of 10", 10, retail=True, wholesale=False),
    Level("TABLET", "Tablet", 1, retail=True, wholesale=False),
]

CAPSULE = [
    Level("CARTON", "Carton of 12 packs", 1200, retail=False, wholesale=True),
    Level("PACK", "Pack of 100 capsules", 100, retail=True, wholesale=True),
    Level("BLISTER", "Blister of 10", 10, retail=True, wholesale=False),
    Level("CAPSULE", "Capsule", 1, retail=True, wholesale=False),
]

# A bottle of syrup is dispensed whole. There is no sub-unit: nobody
# decants 30ml for a patient, so the bottle is the base.
SYRUP = [
    Level("CARTON", "Carton of 24 bottles", 24, retail=False, wholesale=True),
    Level("BOTTLE", "Bottle 100ml", 1, retail=True, wholesale=True),
]

VIAL = [
    Level("CARTON", "Carton of 10 packs", 100, retail=False, wholesale=True),
    Level("PACK", "Pack of 10 vials", 10, retail=True, wholesale=True),
    Level("VIAL", "Vial", 1, retail=True, wholesale=False),
]

TUBE = [
    Level("CARTON", "Carton of 24 tubes", 24, retail=False, wholesale=True),
    Level("TUBE", "Tube 30g", 1, retail=True, wholesale=True),
]

# Gloves are sold by the pair at a counter and by the box to a clinic.
GLOVES = [
    Level("CARTON", "Carton of 10 boxes", 1000, retail=False, wholesale=True),
    Level("BOX", "Box of 100 pairs", 100, retail=True, wholesale=True),
    Level("PAIR", "Pair", 1, retail=True, wholesale=False),
]

PIECE = [
    Level("CARTON", "Carton of 10 boxes", 1000, retail=False, wholesale=True),
    Level("BOX", "Box of 100", 100, retail=True, wholesale=True),
    Level("PIECE", "Piece", 1, retail=True, wholesale=False),
]

STRIP = [
    Level("CARTON", "Carton of 12 tins", 600, retail=False, wholesale=True),
    Level("TIN", "Tin of 50 strips", 50, retail=True, wholesale=True),
    Level("STRIP", "Strip", 1, retail=True, wholesale=False),
]

# A condom is sold as a strip of three at a counter and by the carton to
# a clinic or an NGO programme.
CONDOM = [
    Level("CARTON", "Carton of 144 packs", 432, retail=False, wholesale=True),
    Level("BOX", "Box of 12 packs", 36, retail=True, wholesale=True),
    Level("PACK", "Pack of 3", 3, retail=True, wholesale=False),
    Level("PIECE", "Piece", 1, retail=True, wholesale=False),
]

# Formula and nappies leave in the tin or the pack; nobody breaks them.
TIN = [
    Level("CARTON", "Carton of 12 tins", 12, retail=False, wholesale=True),
    Level("TIN", "Tin 400g", 1, retail=True, wholesale=True),
]

BALE = [
    Level("BALE", "Bale of 4 packs", 4, retail=False, wholesale=True),
    Level("PACK", "Pack", 1, retail=True, wholesale=True),
]

BOTTLE_ML = [
    Level("CARTON", "Carton of 12 bottles", 12, retail=False, wholesale=True),
    Level("BOTTLE", "Bottle", 1, retail=True, wholesale=True),
]

SACHET = [
    Level("CARTON", "Carton of 20 boxes", 400, retail=False, wholesale=True),
    Level("BOX", "Box of 20 sachets", 20, retail=True, wholesale=True),
    Level("SACHET", "Sachet", 1, retail=True, wholesale=False),
]


# Therapeutic classification. A buyer browsing a marketplace filters by
# this before anything else — "show me the antibiotics" is the first
# question, not "show me everything alphabetically".
CATEGORIES = [
    "Anti-infectives",
    "Analgesics and antipyretics",
    "Cardiovascular",
    "Antidiabetics",
    "Respiratory",
    "Gastrointestinal",
    "Dermatological",
    "Ophthalmic",
    "Maternal and reproductive health",
    "Vitamins and supplements",
    "Medical consumables",
    "Diagnostics",
    "Personal care",
    "Sexual and reproductive health",
    "Baby and infant care",
    "First aid and wound care",
    "Oral care",
]


@dataclass(frozen=True)
class Item:
    name: str
    generic: str
    brand: str
    kind: str
    category: str
    legal: str
    tax: str
    cold_chain: bool
    chain: list[Level]
    registration: str
    gtin: str
    #: Cost per base unit, RWF minor units. Retail price is derived.
    unit_cost: int
    #: Typical wholesale price per the pack-level unit.
    pack_price: int


OTC = LegalStatus.OTC
POM = LegalStatus.POM
CTRL = LegalStatus.CONTROLLED
EXEMPT = TaxTreatment.EXEMPT
STANDARD = TaxTreatment.STANDARD

MED = ProductTypeCode.MEDICINE
CONS = ProductTypeCode.CONSUMABLE
DEV = ProductTypeCode.DEVICE
COS = ProductTypeCode.COSMETIC
SUP = ProductTypeCode.SUPPLEMENT


# Medicines are VAT-exempt in Rwanda; cosmetics and general goods are
# standard-rated. That split is why `tax_treatment` is per product and
# not a global setting.
CATALOGUE: list[Item] = [
    # -- anti-infectives ---------------------------------------------------
    Item("Amoxicillin 500mg capsules", "amoxicillin", "Amoxil", MED,
         "Anti-infectives", POM, EXEMPT, False, CAPSULE,
         "RW-MED-2019-0417", "05012345000017", 280, 28000),
    Item("Amoxicillin/Clavulanate 625mg", "co-amoxiclav", "Augmentin", MED,
         "Anti-infectives", POM, EXEMPT, False, TABLET,
         "RW-MED-2020-0663", "05012345000024", 640, 64000),
    Item("Ciprofloxacin 500mg tablets", "ciprofloxacin", "Cipro", MED,
         "Anti-infectives", POM, EXEMPT, False, TABLET,
         "RW-MED-2018-0231", "05012345000031", 310, 31000),
    Item("Metronidazole 400mg tablets", "metronidazole", "Flagyl", MED,
         "Anti-infectives", POM, EXEMPT, False, TABLET,
         "RW-MED-2017-0119", "05012345000048", 145, 14500),
    Item("Artemether/Lumefantrine 20/120mg", "artemether-lumefantrine", "Coartem", MED,
         "Anti-infectives", POM, EXEMPT, False, TABLET,
         "RW-MED-2019-0502", "05012345000055", 520, 52000),
    Item("Amoxicillin 125mg/5ml suspension", "amoxicillin", "Amoxil", MED,
         "Anti-infectives", POM, EXEMPT, False, SYRUP,
         "RW-MED-2019-0418", "05012345000062", 2400, 57600),

    # -- analgesics --------------------------------------------------------
    Item("Paracetamol 500mg tablets", "paracetamol", "Panadol", MED,
         "Analgesics and antipyretics", OTC, EXEMPT, False, TABLET,
         "RW-MED-2015-0044", "05012345000079", 120, 12000),
    Item("Ibuprofen 400mg tablets", "ibuprofen", "Brufen", MED,
         "Analgesics and antipyretics", OTC, EXEMPT, False, TABLET,
         "RW-MED-2016-0088", "05012345000086", 160, 16000),
    Item("Diclofenac 50mg tablets", "diclofenac", "Voltaren", MED,
         "Analgesics and antipyretics", POM, EXEMPT, False, TABLET,
         "RW-MED-2017-0203", "05012345000093", 190, 19000),
    Item("Paracetamol 120mg/5ml syrup", "paracetamol", "Panadol", MED,
         "Analgesics and antipyretics", OTC, EXEMPT, False, SYRUP,
         "RW-MED-2015-0045", "05012345000109", 1800, 43200),
    Item("Morphine 10mg/ml injection", "morphine", "", MED,
         "Analgesics and antipyretics", CTRL, EXEMPT, False, VIAL,
         "RW-MED-2016-0311", "05012345000116", 3400, 34000),

    # -- cardiovascular ----------------------------------------------------
    Item("Amlodipine 5mg tablets", "amlodipine", "Norvasc", MED,
         "Cardiovascular", POM, EXEMPT, False, TABLET,
         "RW-MED-2018-0290", "05012345000123", 210, 21000),
    Item("Atenolol 50mg tablets", "atenolol", "Tenormin", MED,
         "Cardiovascular", POM, EXEMPT, False, TABLET,
         "RW-MED-2016-0155", "05012345000130", 180, 18000),
    Item("Furosemide 40mg tablets", "furosemide", "Lasix", MED,
         "Cardiovascular", POM, EXEMPT, False, TABLET,
         "RW-MED-2015-0071", "05012345000147", 130, 13000),

    # -- antidiabetics -----------------------------------------------------
    Item("Metformin 500mg tablets", "metformin", "Glucophage", MED,
         "Antidiabetics", POM, EXEMPT, False, TABLET,
         "RW-MED-2017-0188", "05012345000154", 150, 15000),
    Item("Insulin glargine 100IU/ml", "insulin glargine", "Lantus", MED,
         "Antidiabetics", POM, EXEMPT, True, VIAL,
         "RW-MED-2020-0741", "05012345000161", 42000, 420000),
    Item("Insulin soluble 100IU/ml", "insulin human", "Actrapid", MED,
         "Antidiabetics", POM, EXEMPT, True, VIAL,
         "RW-MED-2019-0620", "05012345000178", 28000, 280000),

    # -- respiratory -------------------------------------------------------
    Item("Salbutamol 100mcg inhaler", "salbutamol", "Ventolin", MED,
         "Respiratory", POM, EXEMPT, False, TUBE,
         "RW-MED-2018-0245", "05012345000185", 6800, 163200),
    Item("Cetirizine 10mg tablets", "cetirizine", "Zyrtec", MED,
         "Respiratory", OTC, EXEMPT, False, TABLET,
         "RW-MED-2016-0134", "05012345000192", 95, 9500),

    # -- gastrointestinal --------------------------------------------------
    Item("Omeprazole 20mg capsules", "omeprazole", "Losec", MED,
         "Gastrointestinal", POM, EXEMPT, False, CAPSULE,
         "RW-MED-2017-0212", "05012345000208", 240, 24000),
    Item("Oral rehydration salts", "ORS", "", MED,
         "Gastrointestinal", OTC, EXEMPT, False, SACHET,
         "RW-MED-2014-0022", "05012345000215", 320, 6400),

    # -- dermatological ----------------------------------------------------
    Item("Hydrocortisone 1% cream", "hydrocortisone", "", MED,
         "Dermatological", POM, EXEMPT, False, TUBE,
         "RW-MED-2016-0177", "05012345000222", 2100, 50400),
    Item("Clotrimazole 1% cream", "clotrimazole", "Canesten", MED,
         "Dermatological", OTC, EXEMPT, False, TUBE,
         "RW-MED-2017-0199", "05012345000239", 2600, 62400),

    # -- ophthalmic --------------------------------------------------------
    Item("Chloramphenicol 0.5% eye drops", "chloramphenicol", "", MED,
         "Ophthalmic", POM, EXEMPT, False, SYRUP,
         "RW-MED-2018-0266", "05012345000246", 1900, 45600),

    # -- maternal ----------------------------------------------------------
    Item("Ferrous sulphate + folic acid", "ferrous sulphate", "", MED,
         "Maternal and reproductive health", OTC, EXEMPT, False, TABLET,
         "RW-MED-2015-0058", "05012345000253", 85, 8500),
    Item("Combined oral contraceptive", "levonorgestrel/ethinylestradiol", "Microgynon", MED,
         "Maternal and reproductive health", POM, EXEMPT, False, TABLET,
         "RW-MED-2018-0277", "05012345000260", 900, 90000),

    # -- supplements -------------------------------------------------------
    Item("Vitamin C 500mg tablets", "ascorbic acid", "", SUP,
         "Vitamins and supplements", OTC, EXEMPT, False, TABLET,
         "RW-SUP-2019-0031", "05012345000277", 70, 7000),
    Item("Multivitamin syrup", "multivitamin", "", SUP,
         "Vitamins and supplements", OTC, EXEMPT, False, SYRUP,
         "RW-SUP-2018-0018", "05012345000284", 3200, 76800),
    Item("Zinc sulphate 20mg dispersible", "zinc sulphate", "", SUP,
         "Vitamins and supplements", OTC, EXEMPT, False, TABLET,
         "RW-SUP-2020-0044", "05012345000291", 60, 6000),

    # -- consumables and devices -------------------------------------------
    Item("Examination gloves, latex, medium", "", "", CONS,
         "Medical consumables", OTC, STANDARD, False, GLOVES,
         "RW-DEV-2019-0102", "05012345000307", 142, 14200),
    Item("Syringe 5ml with needle", "", "", CONS,
         "Medical consumables", OTC, STANDARD, False, PIECE,
         "RW-DEV-2018-0077", "05012345000314", 95, 9500),
    Item("Adhesive bandage 7.5cm", "", "", CONS,
         "Medical consumables", OTC, STANDARD, False, PIECE,
         "RW-DEV-2017-0055", "05012345000321", 210, 21000),
    Item("Blood glucose test strips", "", "Accu-Chek", DEV,
         "Diagnostics", OTC, STANDARD, False, STRIP,
         "RW-DEV-2020-0131", "05012345000338", 480, 24000),
    Item("Digital thermometer", "", "", DEV,
         "Diagnostics", OTC, STANDARD, False, PIECE,
         "RW-DEV-2019-0118", "05012345000345", 4200, 420000),
    Item("Malaria rapid diagnostic test", "", "", DEV,
         "Diagnostics", OTC, STANDARD, False, PIECE,
         "RW-DEV-2020-0140", "05012345000352", 850, 85000),

    # -- personal care -----------------------------------------------------
    Item("Antiseptic hand rub 500ml", "ethanol", "", COS,
         "Personal care", OTC, STANDARD, False, SYRUP,
         "RW-COS-2020-0026", "05012345000369", 3800, 91200),
    Item("Moisturising lotion 200ml", "", "", COS,
         "Personal care", OTC, STANDARD, False, SYRUP,
         "RW-COS-2019-0014", "05012345000376", 4500, 108000),

    # -- sexual and reproductive health ------------------------------------
    #
    # A pharmacy counter sells far more of this category than of most
    # medicines, and a depot moves it by the carton for public-health
    # programmes. Leaving it out was leaving out the shelf that turns over.
    Item("Male condoms, latex, lubricated", "", "Durex", CONS,
         "Sexual and reproductive health", OTC, STANDARD, False, CONDOM,
         "RW-DEV-2018-0061", "05012345000383", 210, 7560),
    Item("Male condoms, ribbed", "", "Trust", CONS,
         "Sexual and reproductive health", OTC, STANDARD, False, CONDOM,
         "RW-DEV-2019-0088", "05012345000390", 180, 6480),
    Item("Female condoms", "", "", CONS,
         "Sexual and reproductive health", OTC, STANDARD, False, CONDOM,
         "RW-DEV-2020-0112", "05012345000406", 640, 23040),
    Item("Personal lubricant 50ml", "", "", COS,
         "Sexual and reproductive health", OTC, STANDARD, False, TUBE,
         "RW-COS-2020-0033", "05012345000413", 2800, 67200),
    Item("Pregnancy test strip", "", "", DEV,
         "Sexual and reproductive health", OTC, STANDARD, False, PIECE,
         "RW-DEV-2019-0125", "05012345000420", 900, 90000),
    Item("Levonorgestrel 1.5mg emergency", "levonorgestrel", "Postinor", MED,
         "Sexual and reproductive health", POM, EXEMPT, False, TABLET,
         "RW-MED-2019-0533", "05012345000437", 1800, 180000),
    Item("Ovulation test strip", "", "", DEV,
         "Sexual and reproductive health", OTC, STANDARD, False, PIECE,
         "RW-DEV-2021-0146", "05012345000444", 1100, 110000),

    # -- baby and infant care ----------------------------------------------
    Item("Infant formula stage 1, 400g", "", "NAN", SUP,
         "Baby and infant care", OTC, STANDARD, False, TIN,
         "RW-SUP-2019-0052", "05012345000451", 12500, 150000),
    Item("Infant formula stage 2, 400g", "", "NAN", SUP,
         "Baby and infant care", OTC, STANDARD, False, TIN,
         "RW-SUP-2019-0053", "05012345000468", 12800, 153600),
    Item("Baby nappies, medium, pack of 50", "", "Pampers", CONS,
         "Baby and infant care", OTC, STANDARD, False, BALE,
         "RW-DEV-2018-0094", "05012345000475", 9800, 39200),
    Item("Baby wipes, pack of 80", "", "", CONS,
         "Baby and infant care", OTC, STANDARD, False, BALE,
         "RW-DEV-2018-0095", "05012345000482", 3200, 12800),
    Item("Feeding bottle 250ml", "", "", DEV,
         "Baby and infant care", OTC, STANDARD, False, PIECE,
         "RW-DEV-2017-0066", "05012345000499", 4600, 460000),
    Item("Baby barrier cream 100g", "", "", COS,
         "Baby and infant care", OTC, STANDARD, False, TUBE,
         "RW-COS-2018-0009", "05012345000505", 3400, 81600),

    # -- first aid and wound care ------------------------------------------
    Item("Sterile gauze swabs 10x10cm", "", "", CONS,
         "First aid and wound care", OTC, STANDARD, False, PIECE,
         "RW-DEV-2016-0041", "05012345000512", 160, 16000),
    Item("Crepe bandage 7.5cm", "", "", CONS,
         "First aid and wound care", OTC, STANDARD, False, PIECE,
         "RW-DEV-2016-0042", "05012345000529", 780, 78000),
    Item("Cotton wool 100g", "", "", CONS,
         "First aid and wound care", OTC, STANDARD, False, PIECE,
         "RW-DEV-2015-0033", "05012345000536", 620, 62000),
    Item("Povidone iodine 10% solution", "povidone iodine", "Betadine", MED,
         "First aid and wound care", OTC, EXEMPT, False, BOTTLE_ML,
         "RW-MED-2016-0166", "05012345000543", 2900, 34800),
    Item("Surgical tape 2.5cm", "", "", CONS,
         "First aid and wound care", OTC, STANDARD, False, PIECE,
         "RW-DEV-2017-0058", "05012345000550", 540, 54000),
    Item("Elastic adhesive plaster strips", "", "Elastoplast", CONS,
         "First aid and wound care", OTC, STANDARD, False, SACHET,
         "RW-DEV-2018-0079", "05012345000567", 90, 1800),

    # -- oral care ---------------------------------------------------------
    Item("Fluoride toothpaste 100ml", "", "Colgate", COS,
         "Oral care", OTC, STANDARD, False, TUBE,
         "RW-COS-2017-0006", "05012345000574", 2400, 57600),
    Item("Toothbrush, medium", "", "", CONS,
         "Oral care", OTC, STANDARD, False, PIECE,
         "RW-DEV-2016-0047", "05012345000581", 1300, 130000),
    Item("Antiseptic mouthwash 250ml", "chlorhexidine", "", MED,
         "Oral care", OTC, STANDARD, False, BOTTLE_ML,
         "RW-MED-2018-0255", "05012345000598", 4200, 50400),
    Item("Oral rehydration + zinc co-pack", "ORS + zinc", "", MED,
         "Oral care", OTC, EXEMPT, False, SACHET,
         "RW-MED-2019-0544", "05012345000604", 420, 8400),
]


# Manufacturers, with the facts a depot buys on. GMP status can bar an
# import outright, so it is a field rather than a note.
MANUFACTURERS = [
    ("Cipla Ltd", "India", True),
    ("Sun Pharmaceutical", "India", True),
    ("GlaxoSmithKline", "United Kingdom", True),
    ("Sanofi Aventis", "France", True),
    ("Novo Nordisk", "Denmark", True),
    ("Aspen Pharmacare", "South Africa", True),
    ("Kampala Pharmaceutical", "Uganda", True),
    ("Reckitt Benckiser", "United Kingdom", True),
    ("Nestlé Nutrition", "Switzerland", True),
    ("Universal Corporation", "Kenya", True),
]

# Which house makes what. Brand-led where there is a brand, otherwise a
# regional generic manufacturer — which is how most of a Rwandan
# pharmacy's shelf is actually supplied.
_BY_BRAND = {
    "Amoxil": "GlaxoSmithKline",
    "Augmentin": "GlaxoSmithKline",
    "Panadol": "GlaxoSmithKline",
    "Ventolin": "GlaxoSmithKline",
    "Zyrtec": "Sanofi Aventis",
    "Lantus": "Sanofi Aventis",
    "Actrapid": "Novo Nordisk",
    "Cipro": "Cipla Ltd",
    "Flagyl": "Sanofi Aventis",
    "Coartem": "Sun Pharmaceutical",
    "Norvasc": "Cipla Ltd",
    "Tenormin": "Cipla Ltd",
    "Lasix": "Sanofi Aventis",
    "Glucophage": "Sun Pharmaceutical",
    "Losec": "Aspen Pharmacare",
    "Canesten": "Reckitt Benckiser",
    "Betadine": "Reckitt Benckiser",
    "Durex": "Reckitt Benckiser",
    "NAN": "Nestlé Nutrition",
    "Postinor": "Aspen Pharmacare",
}


def manufacturer_for(item: Item) -> str:
    """Who makes it. Falls back to a regional generic house."""
    if item.brand and item.brand in _BY_BRAND:
        return _BY_BRAND[item.brand]
    return "Universal Corporation" if item.kind == MED else "Kampala Pharmaceutical"


# The dosage form follows from the packaging: a chain ending in a vial is
# an injection, one ending in a tablet is a tablet. Deriving it keeps the
# two from ever disagreeing.
_FORM_BY_BASE = {
    "TABLET": "TABLET",
    "CAPSULE": "CAPSULE",
    "BOTTLE": "SYRUP",
    "VIAL": "INJECTION",
    "TUBE": "CREAM",
    "SACHET": "SACHET",
    "STRIP": "DEVICE",
    "PAIR": "DEVICE",
    "PIECE": "DEVICE",
    "TIN": "POWDER",
    "PACK": "DEVICE",
}

_ROUTE_BY_FORM = {
    "TABLET": "ORAL",
    "CAPSULE": "ORAL",
    "SYRUP": "ORAL",
    "SACHET": "ORAL",
    "POWDER": "ORAL",
    "INJECTION": "INTRAVENOUS",
    "CREAM": "TOPICAL",
    "DEVICE": "NOT_APPLICABLE",
}


def form_for(item: Item) -> str:
    base = item.chain[-1].code
    return _FORM_BY_BASE.get(base, "OTHER")


def route_for(item: Item) -> str:
    return _ROUTE_BY_FORM.get(form_for(item), "NOT_APPLICABLE")


def strength_for(item: Item) -> str:
    """Pulled off the name — "Amoxicillin 500mg capsules" → "500mg"."""
    import re

    match = re.search(r"(\d+(?:\.\d+)?\s*(?:mg|mcg|IU|ml|g|%)(?:/\d*\s*ml)?)", item.name, re.I)
    return match.group(1) if match else ""


def storage_for(item: Item) -> tuple[str | None, str | None]:
    """Cold chain is 2–8°C; everything else is below 25°C."""
    return ("2.0", "8.0") if item.cold_chain else (None, "25.0")


def levels_for(item: Item, *, wholesale: bool) -> list[tuple[Level, bool]]:
    """Packaging with sellability resolved for one licence type."""
    return [(level, level.wholesale if wholesale else level.retail) for level in item.chain]
