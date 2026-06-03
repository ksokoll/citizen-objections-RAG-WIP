# Data Governance: Pseudonymisierung und LLM-Verarbeitung

Status: Skizze
Datum: 2026-06-03
Scope: Verarbeitung von Bürger-Einwendungen in der Triage-Pipeline.

Dieses Dokument hält die datenschutzrechtliche Grundlage der
PII-Verarbeitung fest, in Anlehnung an die Struktur einer
Datenschutz-Folgenabschätzung (DSFA, Art. 35 DSGVO). Es ist eine Skizze
für das Demo-Projekt, keine vollständige DSFA. In einem echten
Behördeneinsatz wäre sie durch die DSFA des behördlichen
Datenschutzbeauftragten zu ersetzen. Die technische Umsetzung der
Maskierung steht in ADR-025, das für das rechtliche Warum auf dieses
Dokument verweist.

## 1. Beschreibung der Verarbeitung

Die Behörde verarbeitet im Rahmen der öffentlichen Auslegung eingehende
Bürger-Einwendungen (Masseneinwendungen) zu Bebauungsplänen und
vergleichbaren Vorhaben. Die Pipeline extrahiert per LLM (Triage) die
juristischen Argumente, ordnet Normen zu und erstellt ein deterministisches
Briefing für die Sachbearbeitung. Der Zweck ist die effiziente,
nachvollziehbare Aufbereitung der Einwendungen, nicht die Bewertung oder
Entscheidung über einzelne Bürger.

Als produktives Modell ist Mistral unter maximalen Sicherheitsanforderungen
vorgesehen (europäischer Anbieter).

## 2. Rechtsgrundlage

Die Verarbeitung erfolgt im Rahmen der gesetzlichen Verwaltungsaufgabe der
Behörde (Art. 6 Abs. 1 DSGVO, Verarbeitung zur Erfüllung einer rechtlichen
Verpflichtung bzw. Wahrnehmung einer Aufgabe im öffentlichen Interesse). Die
Einwendung selbst ist der vom Bürger initiierte Verfahrensschritt; ihre
Bearbeitung ist die gesetzliche Aufgabe.

## 3. Pseudonymisierung, nicht Anonymisierung

Die Pipeline arbeitet ausschließlich mit maskiertem Text. Das unmaskierte
Original liegt im zugriffskontrollierten Raw-Store und ist über die
document_id rückführbar (ADR-010).

Damit handelt es sich rechtlich um Pseudonymisierung, nicht Anonymisierung:
die Zuordnung zu einer Person bleibt mit Zusatzinformationen (dem Raw-Store)
möglich. Konsequenz: die maskierten Daten bleiben personenbezogene Daten und
unterliegen weiter der DSGVO. Das ist bewusst so. Ziel der Maskierung ist
Risikominimierung und Datenminimierung, nicht das Verlassen des
DSGVO-Regimes. Die Rückführbarkeit ist für Archiv und Sachbearbeiter-Audit
gewollt.

## 4. Datenminimierung: Umfang der Maskierung

Maßgeblich ist Art. 5 DSGVO (Datenminimierung): es werden die identifizierenden
Merkmale entfernt, die für den Verarbeitungszweck (juristische
Argumentextraktion) nicht erforderlich sind.

Maskiert werden die identifizierenden Kernmerkmale:
- Namen (PERSON)
- Telefonnummern
- E-Mail-Adressen
- IBAN

Nicht maskiert werden Ortsbezüge (LOCATION). Begründung:
- Ein bloßer Ortsname identifiziert in einer regionalen Masseneinwendung
  keine Person; die Behörde erwartet ohnehin den Großteil der Einwendungen
  aus der Region.
- Ortsnamen, Gewässer, Schutzgebiete und Plangebiete sind regelmäßig der
  sachliche Gegenstand der Einwendung (z. B. FFH-Verträglichkeit eines
  benannten Gebiets, Wasserentnahme aus einem benannten Fluss). Ihre
  Maskierung würde den argumentativen Kerngehalt zerstören und damit das
  Datenqualitäts-Gebot verletzen, das der LLM-Input für eine korrekte
  Extraktion braucht.
- Empirisch belegt: eine pauschale Ortsmaskierung führte in einem realen
  Testdokument zu 26 Maskierungen, von denen nur ein kleiner Teil echte
  Adressbestandteile waren und der Rest sachtragende Eigennamen.

Der identifizierende Gehalt einer Adresse liegt in der Kombination Name plus
Straße plus Hausnummer. Da der Name bereits maskiert wird, sinkt der
Identifikationswert verbleibender Ortsangaben deutlich.

## 5. Risiken und Maßnahmen

Verbleibendes Risiko: die NER-basierte Namenserkennung ist nicht perfekt
(deutsches Modell etwa 0,84 F1), einzelne Namen können unmaskiert
durchrutschen. Die Maskierung ist daher eine Verteidigungslinie, nicht die
einzige Schutzmaßnahme.

Maßnahmen:
- Europäisches Modell (Mistral) unter maximalen Sicherheitsanforderungen.
- Zugriffskontrollierter Raw-Store; das unmaskierte Original verlässt die
  Pipeline nicht.
- Mensch in der Schleife: das Briefing geht an die Sachbearbeitung, das
  System trifft keine abschließende Entscheidung über den Bürger.
- Maskierungsgüte ist als Fitness Function messbar (recall-priorisiert, F2),
  Schwellenwert datengetrieben gesetzt (ADR-025).

## 6. EU AI Act

Die Einordnung der Triage in die Risikoklassen des EU AI Act ist eine
Einzelfallprüfung. Behördliche Entscheidungsunterstützung in einem
bürgerrechtsrelevanten Verwaltungsverfahren spricht für eine Einordnung als
Hochrisiko (Anhang III). Der AI Act regelt nicht die Maskierungstiefe; er
verweist für den Datenschutz auf die DSGVO (DSFA nach Art. 35 DSGVO) und
verlangt zusätzlich Nachvollziehbarkeit, Dokumentation, menschliche Aufsicht
und Cybersecurity. Diese Anforderungen werden durch den Audit-Trail, die
Observability und den Sachbearbeiter in der Schleife adressiert.

Kennzeichnungspflichten für KI-generierte Bürger-Texte greifen kaum: das
Briefing geht an die Sachbearbeitung, nicht an den Bürger, und die Endstufe
ist deterministisch ohne LLM-Generierung.

## 7. Vorbehalt

Dieses Dokument ist eine fachlich-technische Skizze, keine
Rechtsberatung. Die verbindliche datenschutzrechtliche Bewertung und die
Einstufung nach EU AI Act obliegen dem behördlichen Datenschutzbeauftragten
bzw. einer juristischen Prüfung im konkreten Einsatz.