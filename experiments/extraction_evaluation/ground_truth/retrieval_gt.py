"""Generate complete retrieval ground truth files from analysed einspruch texts.

For each TYP_2 document, the arguments are derived by hand-analysis of the
einspruch text structure (typically I., II., III. sub-headings or A./B./C./D.
Sachkomplexe). Norm-to-argument mapping uses the fundstelle field from
typ2.json combined with the text structure.

Mixed variants are content-identical copies of their TYP_2 originals, with
einwendungs_typ flipped to "Mixed" and a note about the personal-header overlay.

TYP_1 documents collapse to a single typ1_collective.json file with empty
expected_arguments (per ADR-013, TYP_1 docs are pre-filtered).

All paragraph_ids are validated against available_paragraph_ids.json after
generation.
"""

import json
import shutil
from pathlib import Path
from typing import Any

CATALOG = Path("/home/claude/available_paragraph_ids.json")
OUTPUT_DIR = Path("/home/claude/retrieval_gt_complete")
PILOT_SRC = Path("/mnt/user-data/outputs/retrieval_gt_skeletons/einspruch_11.json")
PILOT_14_LOC = Path("/mnt/user-data/outputs")


# ---------------------------------------------------------------------------
# Document-by-document argument definitions
# ---------------------------------------------------------------------------

def must(pid: str, citation: str, rank: str, rationale: str) -> dict[str, Any]:
    return {
        "paragraph_id": pid,
        "citation": citation,
        "source": "verbatim_in_text",
        "rank_target": rank,
        "rationale": rationale,
    }


def should(pid: str, citation: str, rank: str, rationale: str) -> dict[str, Any]:
    return {
        "paragraph_id": pid,
        "citation": citation,
        "source": "inferred_applicable",
        "rank_target": rank,
        "rationale": rationale,
    }


def nr(citation: str, rationale: str) -> dict[str, Any]:
    """Build a not_retrievable_via_xml entry."""
    return {
        "citation": citation,
        "source": "not_retrievable_via_xml",
        "rationale": rationale,
    }


def arg(
    arg_id: str,
    summary: str,
    anchor: str,
    catalog_id: str,
    must_list: list[dict[str, Any]] | None = None,
    should_list: list[dict[str, Any]] | None = None,
    nr_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "argument_id": arg_id,
        "argument_summary": summary,
        "argument_anchor": anchor,
        "expected_catalog_id": catalog_id,
        "expected_norms": {
            "must_retrieve": must_list or [],
            "should_retrieve": should_list or [],
            "not_retrievable_via_xml": nr_list or [],
        },
    }


# ---------------------------------------------------------------------------
# einspruch_11: Dr. Bertram, Planungsrecht. Klare 3-Argument-Struktur II.1/II.2/II.3.
# ---------------------------------------------------------------------------
EINSPRUCH_11 = {
    "expected_argument_count_range": [3, 3],
    "arguments": [
        arg(
            "arg1",
            "Widerspruch zum Flächennutzungsplan ohne ordnungsgemäßes Parallelverfahren",
            "von dieser Darstellung des Flächennutzungsplans abweicht, ist nach § 8 Abs. 2 BauGB",
            "fnp_widerspruch_kein_parallelverfahren",
            must_list=[
                must(
                    "baugb_§8",
                    "§ 8 Abs. 2 BauGB",
                    "top_1",
                    "Direkte Norm für Entwicklungsgebot und Parallelverfahren bei FNP-Abweichung.",
                ),
            ],
        ),
        arg(
            "arg2",
            "Fehlerhafte Abwägung der Belange Weinbau, Fremdenverkehr und Landschaft",
            "keine hinreichende Auseinandersetzung mit den betroffenen Belangen des Weinbaus",
            "abwaegungsmangel_weinbau_tourismus",
            must_list=[
                must(
                    "baugb_§1",
                    "§ 1 Abs. 7 BauGB",
                    "top_1",
                    "Abwägungsgebot (§ 1 Abs. 7) ist die zentrale Norm. § 1 Abs. 6 Nr. 8 (Weinbau, Fremdenverkehr, Landschaft) ist Sub-Absatz desselben Paragraphen.",
                ),
            ],
        ),
        arg(
            "arg3",
            "Fehlerhafte Festsetzung als Gewerbegebiet statt Sondergebiet",
            "Hyperscale-Rechenzentrum mit einer Netzanschlussleistung von 120 MW ist typologisch kein klassisches Gewerbe",
            "sondergebiet_statt_ge",
            must_list=[
                must(
                    "baunvo_§14",
                    "§ 14 Abs. 2 BauNVO",
                    "top_3",
                    "Fernmeldetechnische Anlagen sind in § 14 Abs. 2 BauNVO geregelt.",
                ),
                must(
                    "baunvo_§11",
                    "§ 11 BauNVO",
                    "top_1",
                    "Sondergebiet als sachgerechte Alternative zur GE-Festsetzung.",
                ),
            ],
            should_list=[
                should(
                    "baunvo_§8",
                    "§ 8 BauNVO",
                    "top_10",
                    "GE-Festsetzung basiert juristisch auf § 8 BauNVO, im Text nicht explizit zitiert.",
                ),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_12: Sauer, Wasserrecht. 3 Argumente: Erlaubnispflicht / Thermik / Grundwasser.
# ---------------------------------------------------------------------------
EINSPRUCH_12 = {
    "expected_argument_count_range": [3, 3],
    "arguments": [
        arg(
            "arg1",
            "Fehlende wasserrechtliche Erlaubnis für Moselentnahme",
            "Jede Entnahme von Wasser aus einem Gewässer bedarf gemäß § 9 Abs. 1 Nr. 1",
            "wasserrechtliche_erlaubnis_fehlt",
            must_list=[
                must(
                    "whg_§9",
                    "§ 9 Abs. 1 Nr. 1 WHG",
                    "top_1",
                    "Erlaubnispflicht für Gewässerbenutzung.",
                ),
                must(
                    "whg_§8",
                    "§ 8 WHG",
                    "top_3",
                    "Erlaubnis als formaler Akt für Gewässerbenutzung.",
                ),
            ],
            nr_list=[
                nr(
                    "Wasserhaushaltsgesetz (WHG)",
                    "Gesetz-Erwähnung ohne Paragraph zur Einordnung Mosel als Bundeswasserstraße.",
                ),
                nr(
                    "Bundeswasserstraßengesetz (WaStrG)",
                    "Gesetz-Erwähnung ohne Paragraph zur Abstimmungspflicht mit WSV.",
                ),
            ],
        ),
        arg(
            "arg2",
            "Thermische Belastung der Mosel durch Kühlwassereinleitung nicht geprüft",
            "Einleitung von Kühlwasser mit erhöhter Temperatur kann zu einer thermischen Belastung",
            "thermische_belastung_mosel",
            must_list=[
                must(
                    "whg_§57",
                    "§ 57 WHG",
                    "top_1",
                    "Prüfpflicht für thermische Gewässerbelastung bei Kühlwassereinleitung.",
                ),
            ],
        ),
        arg(
            "arg3",
            "Fehlende Prüfung der Lage im Trinkwasserschutzgebiet Zone III",
            "Plangebiet liegt im Einzugsbereich eines nach Landesrecht festgesetzten Trinkwasserschutzgebiets",
            "trinkwasserschutzgebiet_zone_iii",
            nr_list=[
                nr(
                    "Landesrechtliche Schutzgebietsverordnung Rheinland-Pfalz",
                    "Landesrechtliche Verordnung, nicht in Bundes-XML enthalten.",
                ),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_13: Nessler, Lärmschutz. 4 Sub-Themen II.1-II.4, keine retrievable §.
# ---------------------------------------------------------------------------
EINSPRUCH_13 = {
    "expected_argument_count_range": [3, 4],
    "arguments": [
        arg(
            "arg1",
            "Fehlende Berücksichtigung tieffrequenter Geräuschanteile (Infraschall)",
            "Rückkühltürme und Großaggregate emittieren ausgeprägte tieffrequente Schallanteile",
            "tieffrequente_geraeusche_din",
            nr_list=[
                nr(
                    "DIN 45680",
                    "Technische Norm für Messung tieffrequenter Geräusche.",
                ),
                nr(
                    "TA Lärm Anhang A.1.5",
                    "Verwaltungsvorschrift-Anhang für tieffrequente Geräusche.",
                ),
            ],
        ),
        arg(
            "arg2",
            "Meteorologische Sondersituationen im Moseltal nicht abgedeckt",
            "Moseltal als topografisch eingeschlossene Tallage sind bei bestimmten Wetterlagen erheblich erhöhte Immissionspegel",
            "meteorologische_ausbreitung_moseltal",
        ),
        arg(
            "arg3",
            "Nachtwertüberschreitung der TA-Lärm-Richtwerte nicht ausgeschlossen",
            "Gemäß TA Lärm gilt für allgemeine Wohngebiete ein Nachtwert von 40 dB(A)",
            "ta_laerm_richtwerte_nachtwert",
            nr_list=[
                nr(
                    "TA Lärm",
                    "Verwaltungsvorschrift TA Lärm, Nachtwert für allgemeines Wohngebiet.",
                ),
            ],
        ),
        arg(
            "arg4",
            "Impulszuschläge für simultanen Notstromaggregat-Anlauf nicht berücksichtigt",
            "simultane Anfahren von bis zu 24 Dieselaggregaten im Notbetrieb stellt ein impulsartiges Schallereignis",
            "impulszuschlaege_notstromaggregate",
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_15: Energie/Netzanschluss. 3 Argumente: Bestätigung / Kosten / Klimaziele.
# ---------------------------------------------------------------------------
EINSPRUCH_15 = {
    "expected_argument_count_range": [3, 3],
    "arguments": [
        arg(
            "arg1",
            "Fehlende verbindliche Netzanschlussbestätigung",
            "verbindliche Netzanschlussbestätigung der Westnetz GmbH oder des zuständigen Übertragungsnetzbetreibers Amprion",
            "netzanschlussbestaetigung_fehlt",
            must_list=[
                must(
                    "enwg_§17",
                    "§ 17 EnWG",
                    "top_1",
                    "Netzanschlussrecht und -pflicht, Voraussetzung jeder Anschlussplanung.",
                ),
            ],
        ),
        arg(
            "arg2",
            "Ungeklärte Kostentragung für Netzausbaumaßnahmen",
            "Wer trägt die Kosten der notwendigen Netzausbaumaßnahmen",
            "netzausbau_kostentragung",
            must_list=[
                must(
                    "enwg_§17",
                    "§ 17 Abs. 3 EnWG",
                    "top_3",
                    "Anschlusskosten vom Anschlussnehmer; selber Paragraph wie arg1, anderer Absatz.",
                ),
            ],
        ),
        arg(
            "arg3",
            "Widerspruch zum kommunalen Klimaschutzkonzept",
            "Klimaschutzkonzepts 2022 eine Reduktion des kommunalen Energieverbrauchs um 30 Prozent bis 2035",
            "klimaschutzkonzept_widerspruch",
            should_list=[
                should(
                    "baugb_§1",
                    "§ 1 Abs. 5 BauGB",
                    "top_10",
                    "Klimaschutz als Belang der Bauleitplanung (impliziter Rechtsbezug).",
                ),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_16: Stadtwerke, Wärme. 1 Argument: Abwärmenutzung nicht geregelt.
# ---------------------------------------------------------------------------
EINSPRUCH_16 = {
    "expected_argument_count_range": [1, 2],
    "arguments": [
        arg(
            "arg1",
            "Fehlende verbindliche Abwärmenutzung im Bebauungsplan und Durchführungsvertrag",
            "verbindliche Klausel im Durchführungsvertrag, die die NordCore Digital GmbH zur Installation einer Wärmeübergabestation",
            "abwaerme_keine_verpflichtung",
            must_list=[
                must(
                    "wpg_§7",
                    "§ 7 WPG",
                    "top_1",
                    "Berücksichtigungspflicht industrieller Abwärmequellen in der kommunalen Wärmeplanung.",
                ),
                must(
                    "baugb_§12",
                    "§ 12 BauGB",
                    "top_3",
                    "Durchführungsvertrag als Verankerungsinstrument für Abwärmeverpflichtung.",
                ),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_17: Moselwein, Weinbau/UNESCO. 4 Argumente.
# ---------------------------------------------------------------------------
EINSPRUCH_17 = {
    "expected_argument_count_range": [3, 4],
    "arguments": [
        arg(
            "arg1",
            "Flächenverlust weinbaulich relevanter Flächen, Präzedenz für Region",
            "Umwidmung dieser Flächen in Gewerbeland setzt einen Präzedenzfall",
            "flaechenverlust_weinbau_praezedenz",
            should_list=[
                should(
                    "baugb_§1",
                    "§ 1 Abs. 6 Nr. 8 BauGB",
                    "top_10",
                    "Belange des Weinbaus implizit, nicht im Text zitiert.",
                ),
            ],
        ),
        arg(
            "arg2",
            "Mikroklimatische Beeinträchtigung der Weinbergsflächen durch Licht und Wärme",
            "Großflächige Industrieanlagen im Bereich von Weinbergslagen können durch artifizielle Belichtung und lokale Wärmeabgabe",
            "mikroklima_weinberg",
        ),
        arg(
            "arg3",
            "Visuelle Beeinträchtigung Landschaftsbild und UNESCO-Welterbe-Antrag",
            "industrielles Großbauwerk von bis zu 25 Metern Höhe in unmittelbarer Sichtachse",
            "unesco_landschaftsbild",
        ),
        arg(
            "arg4",
            "Lage im Landschaftsschutzgebiet Moseltal, Befreiungserfordernis",
            "Teile des Plangebiets liegen nach unserer Prüfung am Rand des Landschaftsschutzgebiets Moseltal",
            "lsg_moseltal_befreiung",
            must_list=[
                must(
                    "bnatschg_§67",
                    "§ 67 BNatSchG",
                    "top_1",
                    "Befreiungsmöglichkeit von LSG-Schutz.",
                ),
            ],
            nr_list=[
                nr(
                    "LSG-Verordnung Kreis Bernkastel-Wittlich",
                    "Landesrechtliche LSG-Verordnung, nicht in Bundes-XMLs.",
                ),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_18: BI Verkehr. 2 Argumente, NULL explizite §, beide inferiert.
# ---------------------------------------------------------------------------
EINSPRUCH_18 = {
    "expected_argument_count_range": [2, 3],
    "arguments": [
        arg(
            "arg1",
            "Unterschätzung und Mängel des Verkehrsgutachtens (Baustellen-, Knoten-, Tourismus-, Schwerlastverkehr)",
            "Aus dem Bericht des Bundesverbandes der Deutschen Zementindustrie zu Rechenzentrum-Neubauten in vergleichbarer Dimension",
            "verkehrsgutachten_maengel",
        ),
        arg(
            "arg2",
            "Ungeklärte Erschließungskosten und Straßenschäden durch Schwerlastverkehr",
            "Wer trägt die Kosten des Ausbaus der Erschließungsstraße und der Ertüchtigung des Knotenpunkts",
            "erschliessungskosten_strassenschaeden",
            should_list=[
                should(
                    "baugb_§30",
                    "§ 30 Abs. 1 BauGB",
                    "top_10",
                    "Erschließungssicherung als planungsrechtliche Voraussetzung (implizit).",
                ),
                should(
                    "baugb_§12",
                    "§ 12 BauGB",
                    "top_10",
                    "Durchführungsvertrag als Instrument zur Regelung von Kostenfragen (implizit).",
                ),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_19: Verfahrensrecht. 4 Argumente II.1-II.4.
# ---------------------------------------------------------------------------
EINSPRUCH_19 = {
    "expected_argument_count_range": [3, 4],
    "arguments": [
        arg(
            "arg1",
            "Unvollständige Auslegung der Planunterlagen (Hydrogeologie, Durchführungsvertrag, EMV)",
            "Hydrogeologische Gutachten, aus dem laut Umweltbericht Aussagen zur Grundwassersituation entnommen wurden",
            "unvollstaendige_auslegung",
            must_list=[
                must(
                    "baugb_§3",
                    "§ 3 Abs. 2 S. 1 BauGB",
                    "top_1",
                    "Auslegungspflicht inkl. wesentlicher umweltbezogener Informationen.",
                ),
                must(
                    "baugb_§214",
                    "§ 214 Abs. 1 Nr. 2 BauGB",
                    "top_3",
                    "Beachtlichkeit des Auslegungsfehlers für Wirksamkeit des B-Plans.",
                ),
            ],
        ),
        arg(
            "arg2",
            "Verkürzte faktische Auslegungszeit (digitale Verfügbarkeit erst ab 10.10.2024)",
            "tatsächliche Auslegungszeit betrug damit effektiv weniger als die gesetzlich vorgeschriebenen 30 Arbeitstage",
            "auslegungszeit_verkuerzt",
        ),
        arg(
            "arg3",
            "Mangelhafte ortsübliche Bekanntmachung (Aushang entfernt, Amtsblatt fehlt)",
            "ortsübliche Bekanntmachung der Auslegung erfolgte durch Aushang am Rathaus",
            "bekanntmachung_mangelhaft",
        ),
        arg(
            "arg4",
            "Fehlende frühzeitige Bürgerbeteiligung",
            "frühzeitige Bürgerbeteiligung hat ausweislich der Verfahrensakte nicht stattgefunden",
            "keine_fruehzeitige_buergerbeteiligung",
            must_list=[
                must(
                    "baugb_§3",
                    "§ 3 Abs. 1 BauGB",
                    "top_1",
                    "Frühzeitige Beteiligung als Pflicht; selber Paragraph wie arg1, anderer Absatz.",
                ),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# einspruch_20: Kanzlei, 4 Sachkomplexe A/B/C/D, 10 Sub-Argumente + 1.
# ---------------------------------------------------------------------------
EINSPRUCH_20 = {
    "expected_argument_count_range": [9, 11],
    "arguments": [
        arg(
            "arg1",
            "A.1 Widerspruch zum FNP, fehlendes Parallelverfahren",
            "Bebauungsplan aus dem Flächennutzungsplan entwickelt sein. Für vorhabenbezogene Bebauungspläne",
            "fnp_widerspruch_kein_parallelverfahren",
            must_list=[
                must("baugb_§8", "§ 8 Abs. 2 BauGB", "top_1",
                     "Entwicklungsgebot und Parallelverfahren bei FNP-Abweichung."),
                must("baugb_§214", "§ 214 Abs. 2 Nr. 2 BauGB", "top_3",
                     "Beachtlichkeit des formellen Fehlers fehlende FNP-Änderung."),
            ],
        ),
        arg(
            "arg2",
            "A.2 Fehlerhafte Gebietsfestsetzung GE statt Sondergebiet",
            "Festsetzung des Plangebiets als Gewerbegebiet ist für das vorliegende Vorhaben typologisch ungeeignet",
            "sondergebiet_statt_ge",
            must_list=[
                must("baunvo_§8", "§ 8 BauNVO", "top_1",
                     "GE-Festsetzung als grundlegend ungeeignet."),
                must("baunvo_§11", "§ 11 BauNVO", "top_3",
                     "Sondergebiet als sachgerechte Alternative."),
            ],
        ),
        arg(
            "arg3",
            "A.3 Fehlende städtebauliche Rechtfertigung, keine Alternativenprüfung",
            "pauschale Bezugnahme auf wirtschaftliche Entwicklungsinteressen der Gemeinde genügt nicht",
            "planrechtfertigung_fehlt",
            must_list=[
                must("baugb_§1", "§ 1 Abs. 3 BauGB", "top_1",
                     "Planrechtfertigung als Grundvoraussetzung."),
            ],
            nr_list=[
                nr("BVerwG Urteil 17.09.2003 Az. 4 C 14.01",
                   "Rechtsprechungsanker für Planrechtfertigungsanforderungen."),
            ],
        ),
        arg(
            "arg4",
            "B.1 Ungeklärter Netzanschluss, Erschließungssicherung fehlt",
            "verbindliche Netzanschlussbestätigung gemäß § 17 EnWG liegt nicht vor",
            "netzanschluss_erschliessung",
            must_list=[
                must("enwg_§17", "§ 17 EnWG", "top_1",
                     "Netzanschluss als Teil der Erschließungssicherung."),
                must("baugb_§30", "§ 30 Abs. 1 BauGB", "top_3",
                     "Erschließungssicherung als Voraussetzung planungsrechtlicher Zulässigkeit."),
            ],
        ),
        arg(
            "arg5",
            "B.2 Fehlende Löschwasserversorgung für Großbrandfall",
            "Löschwasserversorgung laut Auskunft des Landkreises Bernkastel-Wittlich nicht für einen Großbrandfall",
            "loeschwasserversorgung_fehlt",
        ),
        arg(
            "arg6",
            "C.1 Unzulängliche FFH-Verträglichkeitsprüfung",
            "Vorprüfung von lediglich zwei Seiten, die ohne nähere Begründung zum Ergebnis kommt",
            "ffh_vp_unzulaenglich",
            must_list=[
                must("bnatschg_§34", "§ 34 BNatSchG", "top_1",
                     "FFH-Verträglichkeitsprüfung als Pflicht."),
            ],
        ),
        arg(
            "arg7",
            "C.2 Unvollständige Artenschutzprüfung",
            "fehlen Fledermausgutachten für Zugzeiten, eine Reptilienkartierung sowie eine Begehung",
            "artenschutzpruefung_unvollstaendig",
            must_list=[
                must("bnatschg_§44", "§ 44 BNatSchG", "top_1",
                     "Artenschutzrechtliche Zugriffsverbote."),
            ],
            nr_list=[
                nr("Leitfaden Artenschutz Rheinland-Pfalz (MULEWF 2019)",
                   "Landesrechtlicher Fachstandard, nicht in Bundes-XMLs."),
            ],
        ),
        arg(
            "arg8",
            "C.3 Vorhaben unvereinbar mit kommunalem Klimaschutzkonzept",
            "Vorhaben ist mit dem kommunalen Klimaschutzkonzept 2022 der Verbandsgemeinde nicht vereinbar",
            "klimaschutz_widerspruch",
            must_list=[
                must("baugb_§1", "§ 1 Abs. 5 S. 2 BauGB", "top_3",
                     "Klimaschutzziele als Belang der Bauleitplanung; selber Paragraph wie arg3."),
            ],
        ),
        arg(
            "arg9",
            "D.1 Unvollständige Auslegung (Hydrogeologie, Durchführungsvertrag)",
            "vollständige Hydrogeologische Gutachten sowie der Durchführungsvertrag in der Auslegung nicht aus",
            "unvollstaendige_auslegung",
            must_list=[
                must("baugb_§3", "§ 3 Abs. 2 S. 1 BauGB", "top_1",
                     "Auslegungspflicht für Bebauungsplanentwurf und Begründung."),
                must("baugb_§214", "§ 214 Abs. 1 Nr. 2 BauGB", "top_3",
                     "Beachtlichkeit des Auslegungsfehlers; selber Paragraph wie arg1."),
            ],
        ),
        arg(
            "arg10",
            "D.2 Abwägungsdefizit bei touristischen und weinbaulichen Belangen",
            "Touristische Belange und die Bedeutung des Landschaftsbilds für den Weinort Traben-Trarbach werden nicht abgewogen",
            "abwaegungsdefizit_tourismus_weinbau",
            should_list=[
                should("baugb_§1", "§ 1 Abs. 7 BauGB", "top_10",
                       "Abwägungsgebot implizit, im Text nicht zitiert."),
            ],
        ),
        arg(
            "arg11",
            "Fehlende frühzeitige Bürgerbeteiligung (Antrag VI.6)",
            "frühzeitigen Bürgerbeteiligung nach § 3 Abs. 1 BauGB mit öffentlicher Informationsveranstaltung",
            "keine_fruehzeitige_buergerbeteiligung",
            must_list=[
                must("baugb_§3", "§ 3 Abs. 1 BauGB", "top_3",
                     "Frühzeitige Beteiligung, im Antragsteil als Mangel impliziert."),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# Bundle and notes
# ---------------------------------------------------------------------------

TYP_2_DOCS = {
    "einspruch_11": {
        "data": EINSPRUCH_11,
        "schwerpunkt": "Planungsrecht, Bebauungsplan",
        "einreicher": "Dr. Klaus Bertram, Rechtsanwalt",
        "notes_extra": "Klare 3-Argument-Struktur mit Sub-Punkten II.1 bis II.3.",
    },
    "einspruch_12": {
        "data": EINSPRUCH_12,
        "schwerpunkt": "Wasserrecht, Kühlwassernutzung Mosel",
        "einreicher": "Björn Sauer, Dipl.-Ing. Wasserwirtschaft",
        "notes_extra": "Gesetz-Erwähnungen WHG und WaStrG (ohne Paragraph) sind als not_retrievable unter arg1 verbucht. § 47 VwGO ist kein eigenes Argument, sondern Rechtsbehelfsdrohung am Ende.",
    },
    "einspruch_13": {
        "data": EINSPRUCH_13,
        "schwerpunkt": "Lärmschutz, Schallgutachten",
        "einreicher": "Ingrid und Paul Nessler, vertreten durch Dipl.-Ing. Akustik Thomas Reiff",
        "notes_extra": "Edge Case: keine retrievable § Normen vorhanden. Layer-2-Recall ist hier undefiniert. Layer-1-Coverage prüft ob LLM die 4 Sub-Argumente extrahiert.",
    },
    "einspruch_15": {
        "data": EINSPRUCH_15,
        "schwerpunkt": "Energiewirtschaftsrecht, Netzanschluss, Klimaschutz",
        "einreicher": "Initiative Energiefragen Mosel, Stefan Kramer",
        "notes_extra": "arg1 und arg2 referenzieren beide enwg_§17 (verschiedene Absätze). Retriever muss diesen Paragraphen einmal liefern, beide Argumente sind dann erfüllt.",
    },
    "einspruch_16": {
        "data": EINSPRUCH_16,
        "schwerpunkt": "Wärmeplanungsrecht, Abwärmenutzung",
        "einreicher": "Stadtwerke Traben-Trarbach AöR",
        "notes_extra": "Ein einziges kohärentes Argument; Sub-Punkte III.1 bis III.3 sind verschiedene Aspekte derselben Forderung.",
    },
    "einspruch_17": {
        "data": EINSPRUCH_17,
        "schwerpunkt": "Landschaftsschutz, Weinbau, UNESCO",
        "einreicher": "Moselwein e.V., Dr. Karl-Heinz Pontzen",
        "notes_extra": "arg2 (Mikroklima) und arg3 (UNESCO) haben keine spezifischen § Normen, nur Sach-Argumentation.",
    },
    "einspruch_18": {
        "data": EINSPRUCH_18,
        "schwerpunkt": "Verkehr, Erschließung, Infrastrukturkosten",
        "einreicher": "Bürgerinitiative Mosel bleibt Mosel, Erika Feldmann",
        "notes_extra": "Edge Case: null explizite § Normen, alle Bezüge inferiert. Erklärt 0%-Recall-Resultat aus alter Eval. Relevant für Architektur: solche TYP_2-Dokumente existieren in der Praxis.",
    },
    "einspruch_19": {
        "data": EINSPRUCH_19,
        "schwerpunkt": "Verfahrensrecht, Bürgerbeteiligung",
        "einreicher": "Dr. Sabine Wolff",
        "notes_extra": "arg1 und arg4 referenzieren beide baugb_§3 (verschiedene Absätze). Eval-Befund vorher: Extractor findet § 3 BauGB und § 3 Abs. 2 BauGB zusätzlich (Overcount). Volltext-Check ergab: § 3 BauGB ohne Absatz steht in III, § 3 Abs. 2 BauGB ohne S. 1 möglicherweise Pattern-Fragmentierung.",
    },
    "einspruch_20": {
        "data": EINSPRUCH_20,
        "schwerpunkt": "Gesamteinwendung, 4 Sachkomplexe",
        "einreicher": "Kanzlei Franken & Stein, im Auftrag BI Mosel bleibt Mosel",
        "notes_extra": "Komplexester Fall mit 11 Argumenten. § 47 VwGO am Ende ist Rechtsbehelfsdrohung, kein substantielles Argument; daher nicht als arg modelliert.",
    },
}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def is_mixed_id(doc_id: str) -> bool:
    return doc_id.endswith("_mixed")


def build_full_gt(doc_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Compose the final GT JSON for one TYP_2 document."""
    data = payload["data"]
    einw_typ = "Mixed" if is_mixed_id(doc_id) else "TYP_2"
    notes_parts = [
        f"Schwerpunkt: {payload['schwerpunkt']}",
        f"Einreicher: {payload['einreicher']}",
    ]
    if payload.get("notes_extra"):
        notes_parts.append(payload["notes_extra"])
    return {
        "doc_id": doc_id,
        "einwendungs_typ": einw_typ,
        "expected_argument_count_range": data["expected_argument_count_range"],
        "expected_arguments": data["arguments"],
        "notes": ". ".join(notes_parts),
    }


def build_mixed_variant(base_payload: dict[str, Any], source_id: str) -> dict[str, Any]:
    """Mixed variants share content with the TYP_2 original; only metadata changes."""
    new_payload = {
        "data": base_payload["data"],
        "schwerpunkt": base_payload["schwerpunkt"],
        "einreicher": f"Mixed: Inhalt wie {source_id} plus persönlicher Header",
        "notes_extra": (
            f"Mixed-Variante von {source_id}. Argumente, Anchors und Norm-Erwartungen "
            f"sind identisch zum Original. {base_payload.get('notes_extra', '')}"
        ).strip(),
    }
    return new_payload


def build_typ1_collective() -> dict[str, Any]:
    """TYP_1 docs share identical template text; collapsed to one collective GT."""
    return {
        "doc_id": "typ1_collective",
        "einwendungs_typ": "TYP_1",
        "expected_argument_count_range": [0, 0],
        "expected_arguments": [],
        "member_doc_ids": [f"einspruch_{i:02d}" for i in range(1, 11)],
        "notes": (
            "Collective ground truth entry for TYP_1 documents einspruch_01 through "
            "einspruch_10. These are mass-objection (Masseneinwendung) documents with "
            "identical templated text. Per ADR-013, TYP_1 docs are pre-filtered in the "
            "pipeline before argument extraction. The retrieval evaluation does not "
            "apply to TYP_1; expected_arguments is empty by design. Layer 1 (Argument "
            "Coverage) for TYP_1 is evaluated as binary classification accuracy: did "
            "the pre-filter correctly identify the doc as TYP_1?"
        ),
    }


def validate(skeleton: dict[str, Any], catalog_ids: set[str]) -> list[str]:
    missing: list[str] = []
    for argument in skeleton.get("expected_arguments", []):
        for category in ("must_retrieve", "should_retrieve"):
            for entry in argument["expected_norms"][category]:
                pid = entry.get("paragraph_id")
                if pid and pid not in catalog_ids:
                    missing.append(f"{argument['argument_id']}:{pid}")
    return missing


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    with CATALOG.open(encoding="utf-8") as fh:
        catalog = json.load(fh)
    catalog_ids: set[str] = set()
    for gesetz_ids in catalog.values():
        catalog_ids.update(gesetz_ids.keys())

    print(f"Catalog: {len(catalog_ids)} valid paragraph_ids\n")

    all_missing: dict[str, list[str]] = {}
    counts: list[tuple[str, str, int, int, int, int]] = []

    # TYP_2 documents
    for doc_id, payload in TYP_2_DOCS.items():
        full_gt = build_full_gt(doc_id, payload)
        missing = validate(full_gt, catalog_ids)
        if missing:
            all_missing[doc_id] = missing

        out_path = OUTPUT_DIR / f"{doc_id}.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(full_gt, fh, indent=2, ensure_ascii=False)

        n_args = len(full_gt["expected_arguments"])
        n_must = sum(len(a["expected_norms"]["must_retrieve"]) for a in full_gt["expected_arguments"])
        n_should = sum(len(a["expected_norms"]["should_retrieve"]) for a in full_gt["expected_arguments"])
        n_nr = sum(len(a["expected_norms"]["not_retrievable_via_xml"]) for a in full_gt["expected_arguments"])
        counts.append((doc_id, "TYP_2", n_args, n_must, n_should, n_nr))

    # Mixed variants: copy from base TYP_2 docs
    mixed_pairs = [
        ("einspruch_11_mixed", "einspruch_11"),
        ("einspruch_12_mixed", "einspruch_12"),
        ("einspruch_13_mixed", "einspruch_13"),
    ]
    for mixed_id, source_id in mixed_pairs:
        base = TYP_2_DOCS[source_id]
        mixed_payload = build_mixed_variant(base, source_id)
        full_gt = build_full_gt(mixed_id, mixed_payload)
        missing = validate(full_gt, catalog_ids)
        if missing:
            all_missing[mixed_id] = missing

        out_path = OUTPUT_DIR / f"{mixed_id}.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(full_gt, fh, indent=2, ensure_ascii=False)

        n_args = len(full_gt["expected_arguments"])
        n_must = sum(len(a["expected_norms"]["must_retrieve"]) for a in full_gt["expected_arguments"])
        n_should = sum(len(a["expected_norms"]["should_retrieve"]) for a in full_gt["expected_arguments"])
        n_nr = sum(len(a["expected_norms"]["not_retrievable_via_xml"]) for a in full_gt["expected_arguments"])
        counts.append((mixed_id, "Mixed", n_args, n_must, n_should, n_nr))

    # typ1_collective
    typ1 = build_typ1_collective()
    out_path = OUTPUT_DIR / "typ1_collective.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(typ1, fh, indent=2, ensure_ascii=False)
    counts.append(("typ1_collective", "TYP_1", 0, 0, 0, 0))

    # Report
    print(f"{'doc_id':25s} {'typ':7s} {'args':>4s} {'must':>4s} {'should':>6s} {'n_r':>4s}")
    print("-" * 60)
    for doc_id, typ, n_args, n_must, n_should, n_nr in counts:
        print(f"{doc_id:25s} {typ:7s} {n_args:>4d} {n_must:>4d} {n_should:>6d} {n_nr:>4d}")

    print()
    if all_missing:
        print(f"VALIDATION FAILED: {sum(len(v) for v in all_missing.values())} "
              f"paragraph_id references not in catalog:")
        for doc_id, ids in all_missing.items():
            print(f"  {doc_id}: {ids}")
    else:
        print("All paragraph_id references validated against catalog. PASS.")

    print(f"\nGenerated {len(counts)} GT files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()