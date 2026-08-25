> [!IMPORTANT]
> Helft mir bei der Weiterentwicklung und [beantwortet eine Frage](https://github.com/Soardiac/ha-solarmanager/discussions/26) - danke!

# Solar Manager – Home Assistant (Custom Integration)

> **Inoffiziell.** Die Firma Solar Manager AG ist für diesen Code nicht verantwortlich und bietet keinen Support dafür.

Bindet das [Solar Manager](https://www.solar-manager.ch/) Gateway in Home Assistant ein — wahlweise über die **Cloud-API** (voller Funktionsumfang) oder direkt über die **lokale REST-API** (nur Sensoren, kein Internet nötig).

- **Cloud-API**: [cloud.solar-manager.ch](https://external-web.solar-manager.ch/swagger) – voller Funktionsumfang inkl. Steuerung
- **Lokale API**: `GET /v2/point` direkt am Gateway – Sensoren, kein Account nötig
- [**HA Quality Scale**](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/) (Selbsteinschätzung): Bronze ✓ · Silver 95 % (volle Testabdeckung in Arbeit) · Gold: alle anwendbaren Regeln umgesetzt (Repair-Issue für die E-Mail/Passwort-Migration; Discovery nicht anwendbar)

---

## Voraussetzungen

- Home Assistant ≥ 2025.8
- Solar Manager Gateway

**Cloud-Modus** (voller Funktionsumfang):
- Solar Manager Account
- Gateway ID (`smId`, im [Solar Manager Portal](https://web.solar-manager.ch/my-devices/) → Endkunden Information)
- Cloud API Key (Profil → Cloud-API-Schlüssel) — empfohlen; alternativ E-Mail + Passwort

**Lokaler Modus** (nur Sensoren):
- IP-Adresse des Gateways im lokalen Netzwerk
- Kein Account, kein Internet nötig

---

## Installation

### HACS (empfohlen)

1. HACS → Integrationen → `⋮` → **Benutzerdefinierte Repositories**
2. URL: `https://github.com/Soardiac/ha-solarmanager` · Kategorie: **Integration**
3. „Solar Manager" suchen → installieren → Home Assistant neu starten

### Manuell

Ordner `custom_components/solarmanager` in `<config>/custom_components/` kopieren, HA neu starten.

---

## Unterstützte Geräte

Die Integration kommuniziert über das **Solar Manager Gateway** als zentrale Einheit. Je nach Modus stehen unterschiedliche Entitäten zur Verfügung.

| Entitätstyp | Cloud | Lokal |
|---|:---:|:---:|
| Echtzeit-Leistungssensoren (PV, Verbrauch, Netz, Batterie) | ✓ | ✓ ¹ |
| Energie-Intervallwerte (Wh, per Default deaktiviert) | ✓ | ✓ |
| Batterie-SOC, Geräteübersicht | ✓ | ✓ |
| Gerätesensoren (Leistung, SOC, Temperatur, …) | ✓ | ✓ |
| Verbindungsstatus pro Gerät | ✓ | ✓ |
| Tages-Energiesensoren (PV, Verbrauch, Netz, Batterie) | ✓ | ✓ ² |
| Tagesstatistiken (Autarkiegrad, Eigenverbrauchsquote) | ✓ | ✓ ² |
| Betriebsmodi-Select (Wallbox, Batterie, …) | ✓ | – |
| Parameter-Number (SOC-Grenzen, Konstantstrom, …) | ✓ | – |
| Datetime-Entitäten (Ladeziel-Termin) | ✓ | – |

> ¹ Netz Import/Export (W) liefert die lokale API nicht direkt — die Werte werden aus der Energiebilanz berechnet (cW + bcW − pW − bdW).
> ² Im lokalen Modus werden die Tages-Energiewerte durch Integration der Leistungswerte über die Zeit berechnet. Die Tageszähler überleben Neustarts und Reloads (Persistenz auf Disk).

### Unterstützte Gerätetypen

Alle über das Gateway registrierten Geräte werden automatisch erkannt (Cloud und Lokal):

| Gerät | Sensoren | Steuerung |
|---|---|---|
| Batteriespeicher | SOC, Leistung | Eco-/Peak-Shaving-Parameter |
| Wallbox / Car Charger | Leistung, SOC | Lademodus, Konstantstrom, Ladeziel |
| V2X Wallbox | Leistung | Lademodus |
| Wärmepumpe / SG-Ready | Betriebszustand | Betriebsmodus |
| Warmwasserboiler | – | Betriebsmodus, Leistung |
| Smart Plug / Schalter | Schaltzustand | Schaltmodus |
| Wechselrichter | Leistung | Einspeisebegrenzung |
| Weitere Geräte | power / soc / temperature | – |

### Nicht unterstützt

- Geräte, die nicht über ein Solar Manager Gateway registriert sind

---

## Einrichtung

### Cloud API Key erstellen

> **Solar Manager stellt die Authentifizierung per E-Mail/Passwort ein.** Der Cloud API Key ist daher der empfohlene Weg — auch für Neueinrichtungen. E-Mail/Passwort funktioniert weiterhin, aber nur noch bis **30. Juni 2027**; danach ist ein Wechsel auf den API Key erforderlich (siehe [Migration für bestehende Nutzer](#migration-für-bestehende-nutzer)). Wer im Portal keinen Key erstellen kann, richtet die Integration bis dahin mit E-Mail und Passwort ein.

1. Im [Solar Manager Portal](https://web.solar-manager.ch/) → **Profil bearbeiten** → **Cloud-API-Schlüssel** → **API Schlüssel hinzufügen**
2. Neuen Key erstellen:
   - **Enddatum**: leer lassen (kein Ablaufdatum)
   - **Scopes**: alle vier aktivieren: `read`, `write`, `externalOverride:read`, `externalOverride:write`
   - **«Erneuerung erlauben»**: **NICHT** aktivieren — sonst muss der Key regelmässig in HA erneuert werden
3. Den generierten Token **sofort kopieren** — er ist nur direkt nach der Erstellung sichtbar und kann danach nicht mehr abgerufen werden
4. Den Token beim Einrichten der Integration in das Feld **Cloud API Key** einfügen

> **Hinweis:** Falls der Bereich «Cloud-API-Schlüssel» noch nicht sichtbar ist, Solar Manager Support kontaktieren — das Feature wird auf Anfrage freigeschaltet.

### Ersteinrichtung

Einstellungen → Geräte & Dienste → **Integration hinzufügen** → **Solar Manager**

Im ersten Schritt den **Verbindungsmodus** wählen:

#### Cloud-Modus

| Feld | Pflicht | Beschreibung |
|---|---|---|
| Solar Manager ID | Ja | Gateway-ID (`smId`) aus dem Portal |
| Cloud API Key | Ja¹ | Zuvor erstellter API Key (siehe oben) — empfohlener Weg |
| E-Mail | Ja¹ | Nur nötig, wenn kein API Key eingetragen wird |
| Passwort | Ja¹ | Nur nötig, wenn kein API Key eingetragen wird |

> ¹ Als Anmeldung genügt **eines von beidem**: entweder der API Key **oder** E-Mail + Passwort. Wird der API Key eingetragen, hat er Vorrang und E-Mail/Passwort bleiben ungenutzt. Fehlt beides, bricht die Einrichtung mit einem Hinweis ab. Ohne API Key erscheint nach der Einrichtung dauerhaft ein Reparatur-Hinweis auf die Abschaltung am 30. Juni 2027.

#### Lokaler Modus

| Feld | Pflicht | Beschreibung |
|---|---|---|
| IP-Adresse / Hostname | Ja | Gateway-IP im lokalen Netzwerk (z. B. `192.168.1.100`) |
| Protokoll | Ja | `http` oder `https` gemäss Gateway-Einstellungen (Standard: `http`) |
| API Key | Nein | Lokaler API Key falls am Gateway konfiguriert (`X-API-Key`-Header) |

Die Integration testet beim Einrichten direkt die Verbindung (`GET /v2/point`) und meldet einen Fehler, wenn das Gateway nicht erreichbar ist. Bei HTTPS wird das selbst-signierte Zertifikat des Gateways akzeptiert.

> **Hinweis:** Zugangsdaten, `smId`, Host, Protokoll — und auch der Verbindungsmodus selbst (Cloud ↔ Lokal) — lassen sich jederzeit ohne Neueinrichtung ändern (siehe [Neu konfigurieren](#neu-konfigurieren)).

### Neu konfigurieren

Einstellungen → Geräte & Dienste → **Solar Manager** → `⋮` → **Neu konfigurieren**

- **Cloud**: E-Mail, Passwort, `smId` und API Key ändern — leere Felder behalten die gespeicherten Werte. Beim Wechsel der `smId` bleiben die Entitäten erhalten.
- **Lokal**: IP-Adresse/Hostname, Protokoll und API Key ändern (z. B. nach einem IP-Wechsel des Gateways).
- **Wechsel Cloud ↔ Lokal**: Im ersten Schritt von „Neu konfigurieren" den anderen Modus auswählen und die Zugangsdaten für den neuen Modus eingeben. Sensoren und Geräte behalten ihre bestehenden Entity-IDs (Historie bleibt erhalten).

  > **Achtung beim Wechsel auf Lokal:** Betriebsmodi, Parameter und Ladeziel-Termine (Select/Number/Datetime-Entitäten) gibt es nur im Cloud-Modus. Nach dem Wechsel werden sie nicht mehr aktualisiert und sind nicht verfügbar — **Skripte, Automationen und Dashboards, die diese Steuerelemente verwenden, funktionieren dann nicht mehr.** Beim Zurückwechseln zu Cloud werden dieselben Entity-IDs wiederverwendet, alles läuft dann wieder ohne Anpassung.

Die neuen Werte werden vor dem Speichern gegen die API validiert; danach lädt die Integration automatisch neu.

### Migration für bestehende Nutzer

Wer die Integration bisher mit E-Mail/Passwort betrieben hat, kann jederzeit auf den API Key wechseln. Solange kein API Key gesetzt ist, zeigt Home Assistant unter Einstellungen → System → **Reparaturen** proaktiv eine Karte an, die direkt in den Reauth-Dialog führt.

1. API Key wie oben beschrieben erstellen
2. In HA: Einstellungen → Geräte & Dienste → **Solar Manager** → **Neu authentifizieren**
3. API Key eintragen — E-Mail/Passwort-Felder können leer bleiben
4. Bestätigen — die Integration lädt neu und nutzt ab sofort den API Key

---

### Update-Intervall (Optionen)

Nach der Einrichtung: Konfigurieren → **Optionen** → Scan-Intervall in Sekunden (Standard: **10 s**).  
Tagesstatistiken werden alle **5 Minuten** neu geladen. Geräte-Metadaten (Modi, Parameter) werden höchstens alle **60 Sekunden** aktualisiert — nach einem Schreibbefehl aus HA jedoch sofort.

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
| PV-Überschuss | W | PV-Leistung − Hausverbrauch − Batterie-Leistung; positiv = Überschuss |

#### Energie-Intervallwerte

Rohwerte des letzten Stream-Intervalls (≈ 10 s); Klasse `measurement`. Für Dashboards ungeeignet, daher **per Default deaktiviert** — die Tages-Energiesensoren unten sind die richtige Wahl fürs Energie-Dashboard.

| Entität | Einheit | Beschreibung |
|---|---|---|
| PV-Energie (Intervall) | Wh | PV-Ertrag im letzten Intervall |
| Verbrauch (Intervall) | Wh | Verbrauch im letzten Intervall |
| Netzbezug (Intervall) | Wh | Netzbezug im letzten Intervall |
| Netzeinspeisung (Intervall) | Wh | Einspeisung im letzten Intervall |
| Batterie geladen (Intervall) | Wh | Geladene Energie im letzten Intervall |
| Batterie entladen (Intervall) | Wh | Entladene Energie im letzten Intervall |

#### Tages-Energie und Statistiken

Klasse `total_increasing` — direkt im Energie-Dashboard verwendbar. Quelle im Cloud-Modus: `/v1/statistics/gateways` (alle 5 Minuten aktualisiert); im lokalen Modus werden die Werte aus den Leistungsdaten integriert bzw. aus den Intervallwerten summiert. Alle Tageszähler überleben Neustarts und Reloads.

| Entität | Einheit | Cloud | Lokal | Beschreibung |
|---|---|:---:|:---:|---|
| PV Tageserzeugung | Wh | ✓ | ✓ | Gesamterzeugung des heutigen Tages |
| Verbrauch heute | Wh | ✓ | ✓ | Gesamtverbrauch des heutigen Tages |
| Eigenverbrauch heute | Wh | ✓ | ✓ | Direkt selbst genutzter PV-Strom |
| Netzbezug heute | Wh | ✓ | ✓ | Aus dem Netz bezogene Energie |
| Netzeinspeisung heute | Wh | ✓ | ✓ | Ins Netz eingespeiste Energie |
| Batterie geladen heute | Wh | ✓ | ✓ | In die Batterie geladene Energie |
| Batterie entladen heute | Wh | ✓ | ✓ | Aus der Batterie entnommene Energie |
| Eigenverbrauchsquote | % | ✓ | – | Anteil PV-Strom, der selbst verbraucht wurde |
| Autarkiegrad | % | ✓ | – | Anteil des Verbrauchs, der aus PV/Batterie gedeckt wurde |

#### Sonstige Anlage-Sensoren

| Entität | Einheit | Beschreibung |
|---|---|---|
| Batterie-SOC | % | Aktueller Ladestand der Batterie |
| Geräte (Stream-Übersicht) | – | Anzahl der vom Stream gemeldeten Geräte inkl. Rohdaten-Attributen. Diagnose-Sensor, **per Default deaktiviert** |

---

### Geräte (Per-Device, dynamisch)

Pro Gerät werden automatisch Sensoren erstellt, wenn das entsprechende Feld im Stream vorhanden ist. **Neue Geräte werden zur Laufzeit erkannt** — ein Reload oder Neustart ist nicht mehr nötig.

| Sensor | Einheit | Geräteklasse | Bedingung |
|---|---|---|---|
| Leistung | W | Leistung | Feld `power` vorhanden |
| SOC | % | Batterie | Feld `soc` vorhanden |
| Temperatur | °C | Temperatur | Feld `temperature` vorhanden |
| Aktivstatus | – | – | Feld `activeDevice` (1=aktiv/laden, 0=aus, −1=entladen) |
| Tagesverbrauch | Wh | Energie | Feld `iWhTotal` vorhanden |
| Tageseinspeisung | Wh | Energie | Feld `eWhTotal` vorhanden |
| Betriebszustand | – | – | Feld `operationState` (Wärmepumpe) |
| Schaltzustand | – | – | Feld `switchState` vorhanden |
| Heizungskorrektur | – | – | Feld `heatingAdjustment` vorhanden |
| Restreichweite | km | – | Feld `remainingRange` vorhanden |

> **Tagesverbrauch / Tageseinspeisung:** Die Stream-Felder `iWhTotal`/`eWhTotal` sind kumulative
> Zählerstände, die über Tage hinweg weiterlaufen. Die Integration summiert deren Zuwächse und
> setzt die Summe um Mitternacht auf 0 — die Sensoren starten also jeden Tag wieder bei null.
> Der Zwischenstand wird zusammen mit den übrigen Tageszählern gespeichert und übersteht Neustart
> und Reload.
>
> Nicht plausible Sprünge des Zählers zählen dabei nicht mit: ein Rückwärtssprung (Zählerreset im
> Gerät) und ein Zuwachs, der mehr Leistung erfordern würde, als ein Hausgerät aufnehmen kann.
> Letzteres tritt auf, wenn der Stream kurzzeitig `0` meldet und danach wieder den Gesamtstand —
> ohne diese Prüfung landet der komplette Lebenszähler als Tagesverbrauch in der HA-Statistik.

#### Binärsensor: Verbindung

Pro Gerät mit `signal`-Feld: **Ein** = `connected`, **Aus** = getrennt (Diagnose-Kategorie).

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
| Ladeziel kWh Menge | kWh | 1 – 100 | Ladeziel (kWh) |
| Ladeziel kWh Maximum | kWh | 0 – 100 | Ladeziel (kWh) |
| Ladeziel kWh Termin | Datum/Zeit | ISO-Datetime | Ladeziel (kWh) |

#### Warmwasser

| Parameter | Einheit | Bereich |
|---|---|---|
| Leistung | % | 0 – 100 |

---

## Hinweise

- **Modi und Parameter**: Parameter greifen in HA immer, werden von Solar Manager aber nur im jeweils passenden Modus berücksichtigt (z. B. Konstanter Strom nur im Modus „Konstanter Strom").
- **Gerätetypen**: Werden automatisch aus der API erkannt. Unbekannte Typen bekommen keine Steuerentitäten, aber alle verfügbaren Sensoren.
- **Zweisprachig**: Entitätsnamen folgen der HA-Sprache (Deutsch und Englisch enthalten). Die **Optionen** der Modus-Selects (z. B. „Nur Solar") sind bewusst feste Werte, damit Automationen sprachunabhängig stabil bleiben.
- **Batterie-Schreibschutz**: Batterie-Einstellungen werden immer als vollständiges Settings-Objekt geschrieben (read-modify-write). Direkt nach dem Start — bevor die Geräte-Metadaten geladen sind — wird ein Schreibversuch mit einer Fehlermeldung abgelehnt, statt fremde Felder auf Werks-Defaults zurückzusetzen. Ein paar Sekunden warten und erneut versuchen.
- **Cloud-Abhängigkeit (Cloud-Modus)**: Bei Cloud-Ausfall sind alle Werte nicht verfügbar. Der Lokale Modus ist davon nicht betroffen. Ein transient abgelaufener Token wird automatisch erneuert, ohne dass eine Reauth-Aufforderung erscheint.
- **Modus-Wechsel Cloud ↔ Lokal**: Jederzeit über **Neu konfigurieren** möglich, ohne die Integration zu löschen — Entity-IDs und Historie bleiben erhalten (siehe [Neu konfigurieren](#neu-konfigurieren)).
- **API-Doku**: [Swagger](https://external-web.solar-manager.ch/swagger)

---

## Anwendungsfälle

- **PV-Überschuss nutzen**: Gerät (Wallbox, Smart Plug, Boiler) automatisch einschalten, sobald die PV-Leistung den Hausverbrauch übersteigt.
- **Batterie schonen**: Automationen nur ausführen, wenn der Batterie-SOC über einem Schwellwert liegt — verhindert ungewollte Tiefentladung.
- **Tages-Dashboard**: Autarkiegrad und Eigenverbrauchsquote auf einem HA-Dashboard visualisieren und historisch verfolgen.
- **Lastspitzen vermeiden**: Bei hohem Netzbezug eine Benachrichtigung senden oder steuerbare Lasten reduzieren.
- **Anwesenheitsbasiertes Laden**: Wallbox-Lademodus wechseln, wenn jemand nach Hause kommt und SOC unter 50 % liegt.

---

## Beispiele

### Nativer Trigger/Condition: PV-Überschuss (ab HA 2026.7)

Seit HA 2026.7 können Integrationen eigene Trigger/Conditions anbieten (siehe
[Release-Notes](https://www.home-assistant.io/blog/2026/07/01/release-20267/)). Solar Manager
liefert damit `solarmanager.surplus_available` (Trigger) und `solarmanager.is_surplus_present`
(Condition) — beide berechnen den Überschuss (PV-Leistung − Hausverbrauch − Batterie-Leistung)
selbst und bringen einen eingebauten `for`-Debounce (Default 2 Minuten) mit, damit eine einzelne
Wolkenlücke nicht sofort auslöst. Das ersetzt das `numeric_state`-Beispiel weiter unten, ohne dass
man wissen muss, dass negative Netzleistung Einspeisung bedeutet:

```yaml
automation:
  alias: "Wallbox Solar-Modus bei PV-Überschuss"
  trigger:
    - trigger: solarmanager.surplus_available
      target:
        device_id: <device_id des Solar-Manager-Geräts>
      options:
        threshold: 500  # W
        for: "00:02:00"
  action:
    - action: select.select_option
      target:
        entity_id: select.meine_wallbox_modus
      data:
        option: "Nur Solar"
```

Status: experimenteller Spike — Details siehe [Issues](https://github.com/Soardiac/ha-solarmanager/issues).

### Automation: Wallbox auf «Nur Solar» bei PV-Überschuss (klassisch mit `numeric_state`)

```yaml
automation:
  alias: "Wallbox Solar-Modus bei PV-Überschuss"
  trigger:
    - platform: numeric_state
      entity_id: sensor.solarmanager_netzleistung
      below: -500        # > 500 W Einspeisung
      for: "00:02:00"
  action:
    - action: select.select_option
      target:
        entity_id: select.meine_wallbox_modus
      data:
        option: "Nur Solar"
```

### Automation: Benachrichtigung wenn Batterie voll und Export aktiv

```yaml
automation:
  alias: "Benachrichtigung: Batterie voll, PV-Überschuss"
  trigger:
    - platform: numeric_state
      entity_id: sensor.solarmanager_batterie_soc
      above: 95
  condition:
    - condition: numeric_state
      entity_id: sensor.solarmanager_netz_export
      above: 300
  action:
    - action: notify.mobile_app_mein_telefon
      data:
        title: "Solar Manager"
        message: "Batterie voll – {{ states('sensor.solarmanager_netz_export') }} W Überschuss ins Netz."
```
### Dashboard-Beispiel E-Auto und Batterie Lademodi
<img width="1372" height="851" alt="image" src="https://github.com/user-attachments/assets/f296c939-d4c1-4a22-a829-55fd932954f6" />

---

## Fehlerbehebung

### Alle Entities zeigen «Nicht verfügbar»

**Symptom:** Nach dem Start oder nach einer Weile sind alle Sensoren unavailable.  
**Lösung:** Protokoll auf `WARNING`/`ERROR` von `custom_components.solarmanager` prüfen. Häufigste Ursache: Cloud nicht erreichbar oder Zugangsdaten abgelaufen → unter Einstellungen → Geräte & Dienste → **Neu authentifizieren**.

### «Ungültige Anmeldedaten» / Reauth-Benachrichtigung

**Symptom:** HA zeigt automatisch eine Reauth-Aufforderung oder der API Key schlägt fehl.  
**Lösung:** Neuen API Key im Solar Manager Portal erstellen (Profil → Cloud-API-Schlüssel) und unter **Neu authentifizieren** eintragen. Wichtig: Token sofort kopieren, er ist nur einmal sichtbar.

### Ein Gerät taucht nicht als Entity auf

**Symptom:** Gerät ist im Solar Manager Portal sichtbar, aber keine HA-Entity vorhanden.  
**Lösung:** Neue Geräte werden automatisch erkannt, sobald sie im Datenstream bzw. in den Geräte-Metadaten auftauchen (spätestens nach ~60 Sekunden). Erscheint das Gerät dennoch nicht, die Integration neu laden (Einstellungen → Geräte & Dienste → Solar Manager → `⋮` → **Neu laden**) und prüfen, ob das Gerät im Portal korrekt dem Gateway zugeordnet ist.

### Werte aktualisieren sich zu selten

**Symptom:** Sensoren spiegeln den aktuellen Zustand nicht schnell genug wider.  
**Lösung:** Update-Intervall reduzieren: Einstellungen → Geräte & Dienste → Solar Manager → **Konfigurieren** → Scan-Intervall (Minimum empfohlen: 10 s, API-Rate-Limit beachten).

### API Key kann nicht erstellt werden (Bereich nicht sichtbar)

**Symptom:** Kein Menüpunkt «Cloud-API-Schlüssel» im Portal sichtbar.  
**Lösung:** Feature wird auf Anfrage freigeschaltet — Solar Manager Support kontaktieren.

---

## Deinstallation

1. Einstellungen → Geräte & Dienste → **Solar Manager** → `⋮` → **Löschen**
2. Home Assistant neu starten
3. Den Ordner `custom_components/solarmanager` aus `<config>/custom_components/` entfernen (bei manueller Installation) oder die Integration in HACS deinstallieren

---

## Issues & Beiträge

Fehler und Feature-Requests bitte im [Issue Tracker](https://github.com/Soardiac/ha-solarmanager/issues) melden.

---

## Hinweis

Ich habe diese Integration für mich geschrieben, weil keine der anderen passend für mich war. Die Arbeit ist besser investiert, wenn auch andere davon profitieren, darum ist das hier verfügbar und wird gepflegt. Ich verwende Claude Code (oder andere Tools) zur Unterstützung, anders ist es nicht zu machen. KI-Code ist von mir gechecked.
