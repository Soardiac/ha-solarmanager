# Solarmanager – Home Assistant (Custom Integration)

**Inoffiziell — nicht mit Solarmanager verbunden.**

Stellt Live-Daten (PV, Verbrauch, Batterie, Netz, Geräte) und Batterie-Eco-Limits als Entitäten bereit.
Verwendet die Cloud-API von Solarmanager. Du brauchst einen Account dort.

## Installation
**HACS (empfohlen)**
1. HACS → Integrations → `+` → **Custom repositories** → URL dieses Repos, Kategorie *Integration*.
2. „Solarmanager“ suchen, installieren, Home Assistant neu starten.

**Manuell**
- Ordner `custom_components/solarmanager` nach `<config>/custom_components/` kopieren, HA neu starten.

## Einrichtung
- Einstellungen → Geräte & Dienste → Integration hinzufügen → **Solarmanager**
- E-Mail, Passwort, **smId** eintragen.

## Entitäten (Auszug)
- `sensor.solarmanager_*`: PV-Leistung, Verbrauch, Grid, Batterie-SOC, Tages-Energien, Gerätewerte.
- `number.*`: *Eco Entlade-Limit*, *Eco Morgen-Limit*, *Eco Lade-Limit* (setzt `PUT /v2/control/battery/{sensorId}` mit `batteryMode=1`).

## Issues
- Code ist für mich geschrieben, es gibt viele Dinge, die für "ein Produkt" fehlen.
- Robusteres Refresh/Login
- Übersetzungen
- QA (Testing, Linting, Tools,...)
- weitere API endpoints
