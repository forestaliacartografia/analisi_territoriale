# Traduzioni

L'interfaccia di Territorial Suite e' scritta direttamente in **italiano**, la lingua dei
suoi utenti e dei dati che tratta (catasto, vincoli, normativa citata dalle fonti). Il
codice, le API e i commenti sono in inglese.

Questa cartella e' il posto dove andranno i file `.ts`/`.qm` quando verra' aggiunta la
traduzione inglese dell'interfaccia (roadmap V1.0 → V2.0):

```bash
pylupdate5 $(find territorial_suite -name "*.py") -ts i18n/territorial_suite_en.ts
lrelease i18n/territorial_suite_en.ts
```

e in `metadata.txt` si aggiungera' la voce corrispondente. Le stringhe dei messaggi
tecnici (log, errori di servizio) restano in italiano nell'interfaccia e in inglese nel
codice, coerentemente con la separazione fra i due livelli.
