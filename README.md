# Solarmanager – Home Assistant (Custom Integration)

> **Inoffiziell.** Solar Manager AG ist für diesen Code nicht verantwortlich und bietet keinen Support dafür.

Bindet die [Solar Manager](https://www.solar-manager.ch/) Cloud-API in Home Assistant ein. Alle Sensordaten, Betriebsmodi und Geräteparameter stehen als HA-Entitäten zur Verfügung.

- **API**: [cloud.solar-manager.ch](https://external-web.solar-manager.ch/swagger) (Cloud Polling, kein lokaler Zugriff)
- [**HA Quality Scale**](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/): Bronze 100% · Silver 90%

---

## Voraussetzungen

- Home Assistant ≥ 2024.1
- Solar Manager Account
- Solar Manager Gateway ID (`smId`, zu finden im [Solar Manager Portal](https://web.solar-manager.ch/))
- Cloud API Key (im Portal unter Profil → Cloud-API-Schlüssel erstellen)

---

## Installation

### HACS (empfohlen)

1. HACS → Integrationen → `⋮` → **Benutzerdefinierte Repositories**
2. URL: `https://github.com/Soardiac/ha-solarmanager` · Kategorie: **Integration**
3. „Solarmanager" suchen → installieren → Home Assistant neu starten

### Manuell

Ordner `custom_components/solarmanager` in `<config>/custom_components/` kopieren, HA neu starten.

---

## Einrichtung

### Cloud API Key erstellen

> **Solar Manager stellt die Authentifizierung per E-Mail/Passwort ein.** Für Neueinrichtungen ist der Cloud API Key der einzig unterstützte Weg. Bestehende Instanzen mit E-Mail/Passwort funktionieren noch bis **30. Juni 2027** — danach ist ein Wechsel auf den API Key erforderlich (siehe [Migration für bestehende Nutzer](#migration-für-bestehende-nutzer)).

1. Im [Solar Manager Portal](https://web.solar-manager.ch/) → **Profil bearbeiten** → **Cloud-API-Schlüssel** → **API Schlüssel hinzufügen**
2. Neuen Key erstellen:
   - **Enddatum**: leer lassen (kein Ablaufdatum)
   - **Scopes**: alle vier aktivieren: `read`, `write`, `externalOverride:read`, `externalOverride:write`
   - **«Erneuerung erlauben»**: **NICHT** aktivieren — sonst muss der Key regelmässig in HA erneuert werden
3. Den generierten Token **sofort kopieren** — er ist nur direkt nach der Erstellung sichtbar und kann danach nicht mehr abgerufen werden
4. Den Token beim Einrichten der Integration in das Feld **Cloud API Key** einfügen

> **Hinweis:** Falls der Bereich «Cloud-API-Schlüssel» noch nicht sichtbar ist, Solar Manager Support kontaktieren — das Feature wird auf Anfrage freigeschaltet.

### Ersteinrichtung

Einstellungen → Geräte & Dienste → **Integration hinzufügen** → **Solarmanager**

| Feld | Pflicht | Beschreibung |
|---|---|---|
| Solar Manager ID | Ja | Gateway-ID (`smId`) aus dem Portal |
| Cloud API Key | Ja | Zuvor erstellter API Key (siehe oben) |
| E-Mail | Nein | Nur als Fallback wenn noch kein API Key verfügbar |
| Passwort | Nein | Nur als Fallback wenn noch kein API Key verfügbar |

### Migration für bestehende Nutzer

Wer die Integration bisher mit E-Mail/Passwort betrieben hat, kann jederzeit auf den API Key wechseln:

1. API Key wie oben beschrieben erstellen
2. In HA: Einstellungen → Geräte & Dienste → **Solarmanager** → **Neu authentifizieren**
3. API Key eintragen — E-Mail/Passwort-Felder können leer bleiben
4. Bestätigen — die Integration lädt neu und nutzt ab sofort den API Key

---

### Update-Intervall (Optionen)

Nach der Einrichtung: Konfigurieren → **Optionen** → Scan-Intervall in Sekunden (Standard: **10 s**).  
Tagesstatistiken werden alle **5 Minuten** neu geladen.

### Erneute Authentifizierung

Wenn HA einen Auth-Fehler erkennt (abgelaufene Zugangsdaten oder Passwortänderung), erscheint automatisch eine Benachrichtigung. Über den Link darin können die Zugangsdaten aktualisiert werden — ohne die Integration zu löschen.

---

## Entitäten

### Anlage (Site-Level)

Alle Werte beziehen sich auf die gesamte Anlage.

#### Echtzeit-Leistung

| Entität | Einheit | Beschreibung |
|---|---|---|
| PV-Leistung | W | Aktuelle Erzeugungsleistung |
| Hausverbrauch | W | Aktueller Gesamtverbrauch |
| Batterie-Leistung | W | Positiv = Laden, negativ = Entladen |
| Netz Import | W | Bezug aus dem Netz |
| Netz Export | W | Einspeisung ins Netz |
| Netzleistung | W | Positiv = Bezug, negativ = Einspeisung |

#### Energiezähler (Interval)

Werden mit jedem Stream-Poll aktualisiert; Klasse `total_increasing`.

| Entität | Einheit | Beschreibung |
|---|---|---|
| PV-Energie (Interval) | Wh | PV-Ertrag seit letztem Zählerstand |
| Verbrauch (Interval) | Wh | Verbrauch seit letztem Zählerstand |
| Netzbezug (Interval) | Wh | Netzbezug seit letztem Zählerstand |
| Netzeinspeisung (Interval) | Wh | Einspeisung seit letztem Zählerstand |
| Batterie geladen (Interval) | Wh | Geladene Energie seit letztem Stand |
| Batterie entladen (Interval) | Wh | Entladene Energie seit letztem Stand |

#### Tagesstatistiken

Werden alle 5 Minuten aktualisiert (Quelle: `/v1/statistics/gateways`).

| Entität | Einheit | Beschreibung |
|---|---|---|
| PV Tageserzeugung | Wh | Gesamterzeugung des heutigen Tages |
| Verbrauch heute | Wh | Gesamtverbrauch des heutigen Tages |
| Eigenverbrauch heute | Wh | Direkt selbst genutzter PV-Strom |
| Eigenverbrauchsquote | % | Anteil PV-Strom, der selbst verbraucht wurde |
| Autarkiegrad | % | Anteil des Verbrauchs, der aus PV/Batterie gedeckt wurde |

#### Sonstige Anlage-Sensoren

| Entität | Einheit | Beschreibung |
|---|---|---|
| Batterie-SOC | % | Aktueller Ladestand der Batterie |
| Geräte (Stream-Übersicht) | – | Anzahl der vom Stream gemeldeten Geräte |

---

### Geräte (Per-Device, dynamisch)

Pro Gerät werden automatisch Sensoren erstellt, wenn das entsprechende Feld im Stream vorhanden ist.

| Sensor | Einheit | Geräteklasse | Bedingung |
|---|---|---|---|
| Leistung | W | Leistung | Feld `power` vorhanden |
| SOC | % | – | Feld `soc` vorhanden |
| Temperatur | °C | Temperatur | Feld `temperature` vorhanden |
| Aktivstatus | – | – | Feld `activeDevice` (1=aktiv/laden, 0=aus, −1=entladen) |
| Netzbezug heute | kWh | Energie | Feld `iWh` vorhanden |
| Netzeinspeisung heute | kWh | Energie | Feld `eWh` vorhanden |
| Tagesverbrauch | Wh | Energie | Feld `iWhTotal` vorhanden |
| Tageseinspeisung | Wh | Energie | Feld `eWhTotal` vorhanden |
| Betriebszustand | – | – | Feld `operationState` (Wärmepumpe) |
| Schaltzustand | – | – | Feld `switchState` vorhanden |
| Restreichweite | km | – | Feld `remainingRange` vorhanden |

#### Binärsensor: Verbindung

Pro Gerät mit `signal`-Feld: **Ein** = `connected`, **Aus** = getrennt.

---

### Steuerelemente – Betriebsmodi (Select)

Pro Gerät ein Haupt-Modus-Select. Die Optionen hängen vom Gerätetyp ab.

#### Batterie

| Wert | Modus |
|---|---|
| 0 | Standard |
| 1 | Eco |
| 2 | Peak-Shaving |
| 3 | Manuell |
| 4 | Tarif-Optimiert |
| 5 | Standard (aktiv) |
| 6 | KI-Optimierung |

Zusätzlich: **Manuell Richtung** (Select) — Laden / Entladen / AUS

#### Wallbox / Car Charger

Gerätetypen: `car`, `car charger`, `carcharger`, `car charging`, `carcharging`, `ocpp charger`, `wallbox`

| Wert | Modus |
|---|---|
| 0 | Immer laden |
| 1 | Nur Solar |
| 2 | Solar & Tarif |
| 3 | Nie laden |
| 4 | Konstanter Strom |
| 5 | Minimal & Solar |
| 6 | Ladeziel (kWh) |
| 7 | Ladeziel (SoC) |
| 8 | Aria |

#### V2X

| Wert | Modus |
|---|---|
| 0 | Immer laden |
| 1 | Solar-Optimiert |
| 2 | Solar & Tarif |
| 3 | Manuell |
| 4 | Ziel-SOC |

#### Wärmepumpe

Gerätetypen: `heat pump`, `heatpump`, `sg ready switch`

| Wert | Modus |
|---|---|
| 0 | Kein Modus |
| 1 | EIN |
| 2 | AUS |
| 3 | Nur Solar |
| 4 | Solar & Tarif |
| 5 | Keine Steuerung |
| 6 | Normalbetrieb |
| 7 | OEM 14 |
| 8 | KI-Optimierung |

#### Warmwasser

| Wert | Modus |
|---|---|
| 1 | EIN |
| 2 | AUS |
| 3 | Nur Solar |
| 4 | Solar & Tarif |
| 5 | Keine Steuerung |
| 6 | ECO |
| 7 | KI-Optimierung |

#### Smart Plug

| Wert | Modus |
|---|---|
| 1 | EIN |
| 2 | AUS |
| 3 | Nur Solar |
| 4 | Solar & Tarif |
| 5 | Keine Steuerung |

#### Schalter

| Wert | Modus |
|---|---|
| 0 | Kein Modus |
| 1 | EIN |
| 2 | AUS |
| 3 | Nur Solar |
| 4 | Solar & Tarif |
| 5 | Keine Steuerung |

---

### Parameter (Number)

Einstellbare Werte pro Gerät. Die Werte wirken jeweils nur, wenn der passende Modus aktiv ist — das ist die Logik von Solar Manager, nicht von HA.

#### Wechselrichter

| Parameter | Einheit | Bereich |
|---|---|---|
| Einspeisebegrenzung | % | 0 – 100 |

#### Batterie – Eco-Limits

| Parameter | Einheit | Bereich |
|---|---|---|
| Eco Entlade-Limit | % | 0 – 100 |
| Eco Morgen-Limit | % | 0 – 100 |
| Eco Lade-Limit | % | 0 – 100 |

#### Batterie – Allgemeine SOC-Grenzen

| Parameter | Einheit | Bereich |
|---|---|---|
| SOC-Obergrenze | % | 0 – 100 |
| SOC-Untergrenze | % | 0 – 100 |

#### Batterie – Peak-Shaving

| Parameter | Einheit | Bereich | Schritt |
|---|---|---|---|
| Netzlimit | W | 0 – 20 000 | 100 |
| Nachladepower | W | 0 – 20 000 | 100 |
| SOC-Entladegrenze | % | 0 – 100 | 1 |
| SOC-Maximum | % | 0 – 100 | 1 |

#### Batterie – Manuell

| Parameter | Einheit | Bereich | Schritt |
|---|---|---|---|
| Ladeleistung | W | 0 – 20 000 | 100 |
| Entladeleistung | W | 0 – 20 000 | 100 |

#### Batterie – Tarif-Optimiert

| Parameter | Einheit | Bereich | Schritt |
|---|---|---|---|
| Preislimit | CHF/kWh | 0 – 2,00 | 0,01 |
| SOC-Maximum | % | 0 – 100 | 1 |

#### Wallbox / Car Charger

| Parameter | Einheit | Bereich | Modus |
|---|---|---|---|
| Konstantstrom | A | 6 – 32 | Konstanter Strom |
| Ladeziel SOC | % | 0 – 100 | Ladeziel (SoC) |
| Ladeziel SOC Maximum | % | 0 – 100 | Ladeziel (SoC) |
| Ladeziel SOC Termin | Datum/Zeit | ISO-Datetime | Ladeziel (SoC) |
| Ladeziel kWh Menge | % | 1 – 100 | Ladeziel (kWh) |
| Ladeziel kWh Maximum | % | 0 – 100 | Ladeziel (kWh) |
| Ladeziel kWh Termin | Datum/Zeit | ISO-Datetime | Ladeziel (kWh) |

#### Warmwasser

| Parameter | Einheit | Bereich |
|---|---|---|
| Leistung | % | 0 – 100 |

---

## Hinweise

- **Modi und Parameter**: Parameter greifen in HA immer, werden von Solar Manager aber nur im jeweils passenden Modus berücksichtigt (z. B. Konstanter Strom nur im Modus „Konstanter Strom").
- **Gerätetypen**: Werden automatisch aus der API erkannt. Unbekannte Typen bekommen keine Steuerentitäten, aber alle verfügbaren Sensoren.
- **Cloud-Abhängigkeit**: Die Integration kommuniziert ausschliesslich über die Solar Manager Cloud. Bei Cloud-Ausfall sind alle Werte nicht verfügbar.
- **API-Doku**: [Swagger](https://external-web.solar-manager.ch/swagger)

---

## Deinstallation

1. Einstellungen → Geräte & Dienste → **Solarmanager** → `⋮` → **Löschen**
2. Home Assistant neu starten
3. Den Ordner `custom_components/solarmanager` aus `<config>/custom_components/` entfernen (bei manueller Installation) oder die Integration in HACS deinstallieren

---

## Issues & Beiträge

Fehler und Feature-Requests bitte im [Issue Tracker](https://github.com/Soardiac/ha-solarmanager/issues) melden.
