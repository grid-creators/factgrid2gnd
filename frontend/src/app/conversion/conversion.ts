import { Component, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, MarcRecord, MarcDataField, MarcSubfield, DateAlternative, ValidationResult } from '../services/api';

@Component({
  selector: 'app-conversion',
  imports: [CommonModule, FormsModule],
  templateUrl: './conversion.html',
  styleUrl: './conversion.css',
})
export class Conversion {
  qidInput = '';
  dataSource: 'server' | 'local' = 'local';
  field079q: string[] = ['d'];
  field667a = 'Historisches Datenzentrum Sachsen-Anhalt';
  records: MarcRecord[] = [];
  errors: { qid: string; error: string }[] = [];
  selectedIndex = -1;
  loading = false;
  loadingProgress = '';

  // GND warnings per record (preserved across revalidation)
  gndWarnings: Map<string, string[]> = new Map();

  // QIDs of records the user has ticked for selective export
  selectedForExport = new Set<string>();

  // For adding new fields
  showAddField = false;
  newFieldTag = '';
  newFieldInd1 = ' ';
  newFieldInd2 = ' ';
  newFieldSubCode = 'a';
  newFieldSubValue = '';

  // Allowed values for field 079 $q (Teilbestandskennzeichen)
  field079qOptions: { value: string; label: string }[] = [
    { value: 'a', label: 'a — Personennamen der Formalerschließung 1500–1850' },
    { value: 'd', label: 'd — Personennamen aus Dokumentationsbestand' },
    { value: 'e', label: 'e — Personennamen aus osteurop./islam. Kulturkreis' },
    { value: 'f', label: 'f — Formalerschließung' },
    { value: 'g', label: 'g — Gestaltungsmerkmal (DBSM)' },
    { value: 'h', label: 'h — Provenienzkennzeichen' },
    { value: 'l', label: 'l — Namen von Personen in Nachschlagewerken/Lexika' },
    { value: 'm', label: 'm — Musik (zusätzliches Kennzeichen)' },
    { value: 'n', label: 'n — Personennamen des Mittelalters (PMA, gedruckt)' },
    { value: 'o', label: 'o — Personennamen des Mittelalters (PMA, ungedruckt)' },
    { value: 'p', label: 'p — Personennamen der Antike (PAN)' },
    { value: 's', label: 's — Sacherschließung' },
    { value: 't', label: 't — Vorläufige Ansetzung' },
    { value: 'z', label: 'z — Zentralkartei der Autographen (ZKA)' },
  ];

  constructor(private apiService: ApiService, private cdr: ChangeDetectorRef) {}

  get selectedRecord(): MarcRecord | null {
    return this.selectedIndex >= 0 && this.selectedIndex < this.records.length
      ? this.records[this.selectedIndex]
      : null;
  }

  setField079qAt(index: number, value: string): void {
    if (index < 0 || index >= this.field079q.length) return;
    this.field079q[index] = value;
  }

  addField079q(): void {
    const used = new Set(this.field079q);
    const next = this.field079qOptions.find(o => !used.has(o.value));
    this.field079q.push(next ? next.value : 'd');
  }

  removeField079q(index: number): void {
    if (this.field079q.length <= 1) return;
    this.field079q.splice(index, 1);
  }

  parseQids(): string[] {
    return this.qidInput
      .split(/[\n,;\s]+/)
      .map((q) => q.trim().toUpperCase())
      .filter((q) => /^Q\d+$/.test(q));
  }

  convert(): void {
    const qids = this.parseQids();
    if (qids.length === 0) return;

    this.loading = true;
    this.records = [];
    this.errors = [];
    this.gndWarnings.clear();
    this.selectedForExport.clear();
    this.selectedIndex = -1;
    this.loadingProgress = 'Starte Konvertierung...';

    this.apiService.convertStream(qids, this.dataSource, this.field079q, this.field667a).subscribe({
      next: (event) => {
        switch (event.type) {
          case 'progress':
            this.loadingProgress = event.message || '';
            break;
          case 'record':
            if (event.record) {
              // Cache only conversion-time warnings that validate_record() cannot regenerate.
              // Anything validate_record() reproduces (Pflichtfeld, Individualisierung,
              // fehlende GND-Referenz, Dubletten) is left out so it vanishes once the
              // underlying field/subfield is edited or removed.
              const gndW = event.record.validation.warnings.filter(
                (w: string) =>
                  !w.startsWith('Pflichtfeld') &&
                  !w.startsWith('Nur ') &&
                  !w.startsWith('Mindestens') &&
                  !w.startsWith('Dublette') &&
                  !w.startsWith('Feld 548 (') &&
                  !w.includes('hat keine GND-Referenz')
              );
              this.gndWarnings.set(event.record.qid, gndW);
              this.records.push(event.record);
              if (this.selectedIndex < 0) {
                this.selectedIndex = 0;
              }
            }
            break;
          case 'error':
            this.errors.push({ qid: event.qid || '?', error: event.error || 'Unbekannter Fehler' });
            break;
          case 'done':
            this.loading = false;
            this.loadingProgress = '';
            break;
        }
        this.cdr.detectChanges();
      },
      error: () => {
        this.loading = false;
        this.loadingProgress = '';
        this.errors.push({ qid: '', error: 'Verbindung zum Server fehlgeschlagen' });
        this.cdr.detectChanges();
      },
    });
  }

  selectRecord(index: number): void {
    this.selectedIndex = index;
    this.showAddField = false;
  }

  removeRecord(index: number): void {
    if (index < 0 || index >= this.records.length) return;
    const qid = this.records[index].qid;
    this.records.splice(index, 1);
    this.gndWarnings.delete(qid);
    this.selectedForExport.delete(qid);

    // Keep selectedIndex pointing at a valid record
    if (this.records.length === 0) {
      this.selectedIndex = -1;
    } else if (index < this.selectedIndex) {
      this.selectedIndex--;
    } else if (index === this.selectedIndex) {
      this.selectedIndex = Math.min(index, this.records.length - 1);
    }
    this.showAddField = false;
    this.cdr.detectChanges();
  }

  getStatusIcon(record: MarcRecord): string {
    if (record.validation.status === 'error') return 'status-error';
    if (record.validation.warnings.length > 0) return 'status-warn';
    return 'status-ok';
  }

  // Track duplicate QIDs and GND IDs across the current record list
  private getRecordGndId(record: MarcRecord): string {
    const f035 = record.datafields.find(df => df.tag === '035');
    if (!f035) return '';
    const sfA = f035.subfields.find(s => s.code === 'a');
    return sfA ? sfA.value.replace('(DE-588)', '') : '';
  }

  getDuplicateInfo(record: MarcRecord): string {
    const qidCount = this.records.filter(r => r.qid === record.qid).length;
    const gnd = this.getRecordGndId(record);
    // Exclude the "(DE-588)null" placeholder of new records (no real GND-ID yet)
    // so that unrelated new entries are not flagged as duplicates of each other.
    const isRealGnd = !!gnd && gnd !== 'null';
    const gndCount = isRealGnd
      ? this.records.filter(r => this.getRecordGndId(r) === gnd).length
      : 0;
    const parts: string[] = [];
    if (qidCount > 1) parts.push(`QID ${record.qid} ${qidCount}×`);
    if (gndCount > 1) parts.push(`GND ${gnd} ${gndCount}×`);
    return parts.join(', ');
  }

  isDuplicateRecord(record: MarcRecord): boolean {
    return this.getDuplicateInfo(record).length > 0;
  }

  isMissingGndRef(df: MarcDataField): boolean {
    if (df.tag !== '550' && df.tag !== '551') return false;
    return !df.subfields.some(sf => sf.code === '0');
  }

  isMissingCountryCode(df: MarcDataField): boolean {
    if (df.tag !== '043') return false;
    const sfC = df.subfields.find(sf => sf.code === 'c');
    return !sfC || sfC.value.trim() === '';
  }

  getStatusLabel(record: MarcRecord): string {
    if (record.validation.status === 'error') {
      return `${record.validation.warnings.length} Fehler`;
    }
    if (record.validation.warnings.length > 0) {
      return `${record.validation.warnings.length} Warn.`;
    }
    return 'OK';
  }

  // --- GND selection for fields with alternatives ---

  getSelectedGndId(df: MarcDataField): string {
    const sf = df.subfields.find(s => s.code === '0' && s.value.startsWith('(DE-588)'));
    if (!sf) return '';
    return sf.value.replace('(DE-588)', '');
  }

  onGndSelect(df: MarcDataField, gndId: string): void {
    if (!gndId) return;
    // Update $0 subfields with new GND ID
    for (const sf of df.subfields) {
      if (sf.code === '0' && sf.value.startsWith('(DE-588)')) {
        sf.value = `(DE-588)${gndId}`;
      } else if (sf.code === '0' && sf.value.startsWith('https://d-nb.info/gnd/')) {
        sf.value = `https://d-nb.info/gnd/${gndId}`;
      }
    }
    // Update $a subfield with the label of the selected GND alternative
    const alt = df.gnd_alternatives?.find(a => a.id === gndId);
    if (alt) {
      const sfA = df.subfields.find(s => s.code === 'a');
      if (sfA) {
        sfA.value = alt.label;
      }
    }
    // Drop the cached "mehrere GND-IDs" warning for this field — the user has chosen.
    if (this.selectedRecord && df.gnd_alternatives && df.gnd_alternatives.length > 0) {
      const qid = this.selectedRecord.qid;
      const altIds = df.gnd_alternatives.map(a => a.id);
      const cached = this.gndWarnings.get(qid) || [];
      const filtered = cached.filter(w => {
        if (!w.startsWith(`Feld ${df.tag} `)) return true;
        return !altIds.some(id => w.includes(id));
      });
      this.gndWarnings.set(qid, filtered);
    }
    this.cdr.detectChanges();
    this.revalidate();
  }

  // --- Date selection for 548 datl with multiple date claims ---
  // datl (approximate life dates) is NOT repeatable: single-select, the
  // preferred-rank range is pre-filled by the backend as the default.

  hasDateAlternatives(df: MarcDataField): boolean {
    return df.tag === '548' && !!df.date_alternatives && df.date_alternatives.length > 1
      && df.subfields.some(sf => sf.code === '4' && sf.value === 'datl');
  }

  hasAnyDateAlternatives(): boolean {
    if (!this.selectedRecord) return false;
    return this.selectedRecord.datafields.some(
      df => this.hasDateAlternatives(df)
        || this.hasDateAlternativesDatx(df)
        || this.hasDateAlternativesDatw(df)
        || this.hasDateAlternativesDatz(df)
    );
  }

  isDatePickerAnchor(index: number): boolean {
    if (!this.hasAnyDateAlternatives() || !this.selectedRecord) return false;
    const dfs = this.selectedRecord.datafields;
    // Anchor = last 548 datl field; fall back to last 548 of any kind if no datl exists.
    let lastDatl = -1;
    let last548 = -1;
    for (let i = 0; i < dfs.length; i++) {
      if (dfs[i].tag !== '548') continue;
      last548 = i;
      if (dfs[i].subfields.some(sf => sf.code === '4' && sf.value === 'datl')) {
        lastDatl = i;
      }
    }
    const anchor = lastDatl >= 0 ? lastDatl : last548;
    return index === anchor;
  }

  isDateSelected(df: MarcDataField, dateValue: string): boolean {
    if (!this.selectedRecord || !dateValue) return false;
    return this.selectedRecord.datafields.some(
      f => f.tag === '548'
        && f.subfields.some(sf => sf.code === '4' && sf.value === 'datl')
        && f.subfields.some(sf => sf.code === 'a' && sf.value === dateValue)
    );
  }

  onDatlSelect(df: MarcDataField, dateValue: string): void {
    if (!this.selectedRecord || !dateValue) return;
    // datl is non-repeatable: just replace the $a of the single datl field.
    const sfA = df.subfields.find(sf => sf.code === 'a');
    if (sfA) sfA.value = dateValue;
    // The life-date range is also carried in $d of the name fields 100/400.
    this.applyLifeDatesToNames(dateValue);
    // The "bitte ein Datum waehlen" hint is regenerable (validate_record); it
    // clears automatically on revalidate now that $a is filled.
    this.cdr.detectChanges();
    this.revalidate();
  }

  // Propagate the 548 datl life-date range ($a) to the $d subfield of the
  // preferred name (100) and all variant names (400), so the name fields stay
  // in sync when the user changes datl. Empty value removes the $d.
  private applyLifeDatesToNames(dateValue: string): void {
    if (!this.selectedRecord) return;
    for (const f of this.selectedRecord.datafields) {
      if (f.tag !== '100' && f.tag !== '400') continue;
      const dIdx = f.subfields.findIndex(sf => sf.code === 'd');
      if (!dateValue) {
        if (dIdx >= 0) f.subfields.splice(dIdx, 1);
        continue;
      }
      if (dIdx >= 0) {
        f.subfields[dIdx].value = dateValue;
      } else {
        const aIdx = f.subfields.findIndex(sf => sf.code === 'a');
        f.subfields.splice(aIdx + 1, 0, { code: 'd', value: dateValue });
      }
    }
  }

  // --- Date selection for 548 datx (multi-select, mirrors datl) ---

  hasDateAlternativesDatx(df: MarcDataField): boolean {
    return df.tag === '548' && !!df.date_alternatives && df.date_alternatives.length > 1
      && df.subfields.some(sf => sf.code === '4' && sf.value === 'datx');
  }

  isDatxSelected(df: MarcDataField, dateValue: string): boolean {
    if (!this.selectedRecord || !dateValue) return false;
    return this.selectedRecord.datafields.some(
      f => f.tag === '548'
        && f.subfields.some(sf => sf.code === '4' && sf.value === 'datx')
        && f.subfields.some(sf => sf.code === 'a' && sf.value === dateValue)
    );
  }

  onDatxToggle(df: MarcDataField, dateValue: string, checked: boolean): void {
    if (!this.selectedRecord) return;

    if (checked) {
      const emptyDatx = this.selectedRecord.datafields.find(
        f => f.tag === '548'
          && f.subfields.some(sf => sf.code === '4' && sf.value === 'datx')
          && f.subfields.some(sf => sf.code === 'a' && sf.value === '')
      );
      if (emptyDatx) {
        const sfA = emptyDatx.subfields.find(sf => sf.code === 'a');
        if (sfA) sfA.value = dateValue;
      } else {
        const newField: MarcDataField = {
          tag: '548',
          ind1: ' ',
          ind2: ' ',
          subfields: [
            { code: 'a', value: dateValue },
            { code: '4', value: 'datx' },
            { code: '4', value: 'https://d-nb.info/standards/elementset/gnd#dateOfBirthAndDeath' },
            { code: 'w', value: 'r' },
            { code: 'i', value: 'Exakte Lebensdaten' },
          ],
        };
        const dfs = this.selectedRecord.datafields;
        const lastDatxIdx = dfs.reduce(
          (acc, f, i) => f.tag === '548'
            && f.subfields.some(sf => sf.code === '4' && sf.value === 'datx') ? i : acc, -1
        );
        dfs.splice(lastDatxIdx + 1, 0, newField);
      }
    } else {
      const idx = this.selectedRecord.datafields.findIndex(
        f => f.tag === '548'
          && f.subfields.some(sf => sf.code === '4' && sf.value === 'datx')
          && f.subfields.some(sf => sf.code === 'a' && sf.value === dateValue)
      );
      if (idx >= 0) {
        const removed = this.selectedRecord.datafields[idx];
        const otherDatx = this.selectedRecord.datafields.filter(
          (f, i) => i !== idx && f.tag === '548'
            && f.subfields.some(sf => sf.code === '4' && sf.value === 'datx')
        );
        if (otherDatx.length === 0) {
          // Keep the alternatives-carrying field but clear $a
          const sfA = removed.subfields.find(sf => sf.code === 'a');
          if (sfA) sfA.value = '';
        } else {
          if (removed.date_alternatives) {
            otherDatx[0].date_alternatives = removed.date_alternatives;
          }
          this.selectedRecord.datafields.splice(idx, 1);
        }
      }
    }

    // The "bitte zutreffende Werte waehlen" prompt is regenerable
    // (validate_record), so it reappears automatically when all datx options
    // are deselected and clears when one is selected — no manual cache handling.
    this.cdr.detectChanges();
    this.revalidate();
  }

  // --- Date selection for 548 datw (activity dates, single-select fallback) ---
  // datw is offered only when no life dates exist; like datl it is single-select
  // with the first candidate (P1504/P1505 range) pre-filled by the backend.

  hasDateAlternativesDatw(df: MarcDataField): boolean {
    return df.tag === '548' && !!df.date_alternatives && df.date_alternatives.length > 1
      && df.subfields.some(sf => sf.code === '4' && sf.value === 'datw');
  }

  isDatwSelected(df: MarcDataField, dateValue: string): boolean {
    if (!this.selectedRecord || !dateValue) return false;
    return this.selectedRecord.datafields.some(
      f => f.tag === '548'
        && f.subfields.some(sf => sf.code === '4' && sf.value === 'datw')
        && f.subfields.some(sf => sf.code === 'a' && sf.value === dateValue)
    );
  }

  onDatwSelect(df: MarcDataField, dateValue: string): void {
    if (!this.selectedRecord || !dateValue) return;
    const sfA = df.subfields.find(sf => sf.code === 'a');
    if (sfA) sfA.value = dateValue;
    this.cdr.detectChanges();
    this.revalidate();
  }

  // --- Date selection for 548 datz (exact activity dates, multi-select) ---
  // datz mirrors datx: exact (month/day) activity dates, repeatable. Offered
  // only when no life dates exist and several exact activity dates are available.

  hasDateAlternativesDatz(df: MarcDataField): boolean {
    return df.tag === '548' && !!df.date_alternatives && df.date_alternatives.length > 1
      && df.subfields.some(sf => sf.code === '4' && sf.value === 'datz');
  }

  isDatzSelected(df: MarcDataField, dateValue: string): boolean {
    if (!this.selectedRecord || !dateValue) return false;
    return this.selectedRecord.datafields.some(
      f => f.tag === '548'
        && f.subfields.some(sf => sf.code === '4' && sf.value === 'datz')
        && f.subfields.some(sf => sf.code === 'a' && sf.value === dateValue)
    );
  }

  onDatzToggle(df: MarcDataField, dateValue: string, checked: boolean): void {
    if (!this.selectedRecord) return;

    if (checked) {
      const emptyDatz = this.selectedRecord.datafields.find(
        f => f.tag === '548'
          && f.subfields.some(sf => sf.code === '4' && sf.value === 'datz')
          && f.subfields.some(sf => sf.code === 'a' && sf.value === '')
      );
      if (emptyDatz) {
        const sfA = emptyDatz.subfields.find(sf => sf.code === 'a');
        if (sfA) sfA.value = dateValue;
      } else {
        const newField: MarcDataField = {
          tag: '548',
          ind1: ' ',
          ind2: ' ',
          subfields: [
            { code: 'a', value: dateValue },
            { code: '4', value: 'datz' },
            { code: '4', value: 'https://d-nb.info/standards/elementset/gnd#periodOfActivity' },
            { code: 'w', value: 'r' },
            { code: 'i', value: 'Exakte Wirkungsdaten' },
          ],
        };
        const dfs = this.selectedRecord.datafields;
        const lastDatzIdx = dfs.reduce(
          (acc, f, i) => f.tag === '548'
            && f.subfields.some(sf => sf.code === '4' && sf.value === 'datz') ? i : acc, -1
        );
        dfs.splice(lastDatzIdx + 1, 0, newField);
      }
    } else {
      const idx = this.selectedRecord.datafields.findIndex(
        f => f.tag === '548'
          && f.subfields.some(sf => sf.code === '4' && sf.value === 'datz')
          && f.subfields.some(sf => sf.code === 'a' && sf.value === dateValue)
      );
      if (idx >= 0) {
        const removed = this.selectedRecord.datafields[idx];
        const otherDatz = this.selectedRecord.datafields.filter(
          (f, i) => i !== idx && f.tag === '548'
            && f.subfields.some(sf => sf.code === '4' && sf.value === 'datz')
        );
        if (otherDatz.length === 0) {
          const sfA = removed.subfields.find(sf => sf.code === 'a');
          if (sfA) sfA.value = '';
        } else {
          if (removed.date_alternatives) {
            otherDatz[0].date_alternatives = removed.date_alternatives;
          }
          this.selectedRecord.datafields.splice(idx, 1);
        }
      }
    }

    // The "bitte zutreffende Werte waehlen" prompt is regenerable
    // (validate_record), so it reappears automatically when all datz options
    // are deselected and clears when one is selected — no manual cache handling.
    this.cdr.detectChanges();
    this.revalidate();
  }

  // --- Warning navigation ---

  getWarningTag(warning: string): string | null {
    // "Pflichtfeld 043 (...)" or "Feld 551 (...)"
    const m = warning.match(/^(?:Pflichtfeld|Feld)\s+(\d{3})/);
    return m ? m[1] : null;
  }

  scrollToWarning(warning: string): void {
    const tag = this.getWarningTag(warning);
    if (!tag || !this.selectedRecord) return;

    // Extract quoted name from warning to find exact field, e.g. "Erfurt"
    const nameMatch = warning.match(/["\u201e]([^"\u201c\u201d]+)["\u201c\u201d]/);
    const name = nameMatch ? nameMatch[1] : null;

    // Try control fields first
    const cfEl = document.getElementById('field-' + tag);
    if (cfEl) {
      cfEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      cfEl.classList.add('highlight-flash');
      setTimeout(() => cfEl.classList.remove('highlight-flash'), 1500);
      return;
    }

    // Find matching datafield index
    const dfs = this.selectedRecord.datafields;
    let targetIndex = -1;
    if (name) {
      targetIndex = dfs.findIndex(df =>
        df.tag === tag && df.subfields.some(sf => sf.code === 'a' && sf.value === name)
      );
    }
    if (targetIndex < 0) {
      targetIndex = dfs.findIndex(df => df.tag === tag);
    }
    if (targetIndex < 0) return;

    const el = document.getElementById('datafield-' + targetIndex);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('highlight-flash');
      setTimeout(() => el.classList.remove('highlight-flash'), 1500);
    }
  }

  // --- Editing ---

  onControlFieldChange(): void {
    this.revalidate();
  }

  onSubfieldChange(df?: MarcDataField, sf?: MarcSubfield): void {
    // When the datl $a (life-date range) is edited directly in the field editor,
    // propagate the new value to $d of the name fields 100/400.
    if (df && sf && sf.code === 'a' && df.tag === '548'
        && df.subfields.some(s => s.code === '4' && s.value === 'datl')) {
      this.applyLifeDatesToNames(sf.value);
    }
    this.revalidate();
  }

  removeDataField(fieldIndex: number): void {
    if (!this.selectedRecord) return;
    this.selectedRecord.datafields.splice(fieldIndex, 1);
    this.revalidate();
  }

  removeSubfield(fieldIndex: number, subIndex: number): void {
    if (!this.selectedRecord) return;
    const field = this.selectedRecord.datafields[fieldIndex];
    field.subfields.splice(subIndex, 1);
    if (field.subfields.length === 0) {
      this.selectedRecord.datafields.splice(fieldIndex, 1);
    }
    this.revalidate();
  }

  addSubfield(fieldIndex: number): void {
    if (!this.selectedRecord) return;
    this.selectedRecord.datafields[fieldIndex].subfields.push({
      code: 'a',
      value: '',
    });
  }

  toggleAddField(): void {
    this.showAddField = !this.showAddField;
    this.newFieldTag = '';
    this.newFieldInd1 = ' ';
    this.newFieldInd2 = ' ';
    this.newFieldSubCode = 'a';
    this.newFieldSubValue = '';
  }

  addNewField(): void {
    if (!this.selectedRecord || !this.newFieldTag) return;

    const newField: MarcDataField = {
      tag: this.newFieldTag,
      ind1: this.newFieldInd1 || ' ',
      ind2: this.newFieldInd2 || ' ',
      subfields: [
        {
          code: this.newFieldSubCode || 'a',
          value: this.newFieldSubValue,
        },
      ],
    };

    this.selectedRecord.datafields.push(newField);
    // Sort by tag
    this.selectedRecord.datafields.sort((a, b) => a.tag.localeCompare(b.tag));
    this.showAddField = false;
    this.revalidate();
  }

  // --- Validation ---

  revalidate(): void {
    if (!this.selectedRecord) return;
    const qid = this.selectedRecord.qid;
    this.apiService.validateRecord(this.selectedRecord).subscribe({
      next: (validation) => {
        const gndW = this.gndWarnings.get(qid) || [];
        validation.warnings = [...gndW, ...validation.warnings];
        this.selectedRecord!.validation = validation;
        this.cdr.detectChanges();
      },
    });
  }

  // --- Export gating ---

  exportBlockReasons(record: MarcRecord | null): string[] {
    if (!record) return ['Kein Record ausgewaehlt'];
    const reasons: string[] = [];

    // 1. Individualisierung 3/3
    if (record.validation.individualization_count < 3) {
      reasons.push(`Individualisierung ${record.validation.individualization_count}/3`);
    }

    // 2. Person darf keine GND-ID haben (035 $a == "(DE-588)null")
    const f035 = record.datafields.find(df => df.tag === '035');
    const sf035a = f035?.subfields.find(s => s.code === 'a');
    const gnd035 = sf035a ? sf035a.value.replace('(DE-588)', '').trim() : '';
    if (gnd035 && gnd035 !== 'null') {
      reasons.push(`Person hat bereits GND-ID ${gnd035}`);
    }

    // 3. 043 muss einen Wert haben
    const f043 = record.datafields.find(df => df.tag === '043');
    const sf043c = f043?.subfields.find(s => s.code === 'c');
    if (!sf043c || !sf043c.value.trim()) {
      reasons.push('Feld 043 (Laendercode) fehlt oder leer');
    }

    // 4. berc-Feld (550 $4=berc) muss eine GND-ID haben
    const bercField = record.datafields.find(df =>
      df.tag === '550' && df.subfields.some(s => s.code === '4' && s.value === 'berc')
    );
    if (bercField) {
      const hasGnd = bercField.subfields.some(s =>
        s.code === '0' && s.value.startsWith('(DE-588)') &&
        s.value.replace('(DE-588)', '').trim() !== ''
      );
      if (!hasGnd) reasons.push('Charakteristischer Beruf (550 $4=berc) ohne GND-ID');
    }

    return reasons;
  }

  canExportRecord(record: MarcRecord | null): boolean {
    return this.exportBlockReasons(record).length === 0;
  }

  canExportCurrent(): boolean {
    return this.canExportRecord(this.selectedRecord);
  }

  canExportAll(): boolean {
    return this.records.length > 0 && this.records.every(r => this.canExportRecord(r));
  }

  exportAllBlockReason(): string {
    if (this.records.length === 0) return 'Keine Records';
    const blocked = this.records.filter(r => !this.canExportRecord(r));
    if (blocked.length === 0) return '';
    return `${blocked.length} von ${this.records.length} Records nicht exportierbar (z. B. ${blocked[0].qid})`;
  }

  // --- Selection for export ---

  isSelectedForExport(qid: string): boolean {
    return this.selectedForExport.has(qid);
  }

  toggleExportSelection(qid: string, checked: boolean): void {
    if (checked) this.selectedForExport.add(qid);
    else this.selectedForExport.delete(qid);
  }

  allSelectedForExport(): boolean {
    return this.records.length > 0 && this.selectedForExport.size === this.records.length;
  }

  toggleAllExportSelection(): void {
    if (this.allSelectedForExport()) {
      this.selectedForExport.clear();
    } else {
      this.selectedForExport = new Set(this.records.map(r => r.qid));
    }
  }

  canExportSelected(): boolean {
    if (this.selectedForExport.size === 0) return false;
    return this.records
      .filter(r => this.selectedForExport.has(r.qid))
      .every(r => this.canExportRecord(r));
  }

  exportSelectedBlockReason(): string {
    if (this.selectedForExport.size === 0) return 'Keine Personen ausgewaehlt';
    const blocked = this.records
      .filter(r => this.selectedForExport.has(r.qid) && !this.canExportRecord(r));
    if (blocked.length === 0) return '';
    return `${blocked.length} der ausgewaehlten Records nicht exportierbar (z. B. ${blocked[0].qid})`;
  }

  exportSelected(): void {
    const toExport = this.records.filter(r => this.selectedForExport.has(r.qid));
    if (toExport.length === 0) return;
    this.apiService.exportRecords(toExport).subscribe({
      next: (blob) => this.downloadBlob(blob, 'gnd_export_auswahl.mrcx'),
      error: () => alert('Export fehlgeschlagen'),
    });
  }

  // --- Export ---

  exportCurrent(): void {
    if (!this.selectedRecord) return;
    this.apiService.exportRecords([this.selectedRecord]).subscribe({
      next: (blob) => this.downloadBlob(blob, `${this.selectedRecord!.qid}_gnd.mrcx`),
      error: () => alert('Export fehlgeschlagen'),
    });
  }

  exportAll(): void {
    if (this.records.length === 0) return;
    this.apiService.exportRecords(this.records).subscribe({
      next: (blob) => this.downloadBlob(blob, 'gnd_export.mrcx'),
      error: () => alert('Export fehlgeschlagen'),
    });
  }

  private downloadBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }
}
