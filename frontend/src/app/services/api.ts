import { Injectable, NgZone } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';


export interface MarcSubfield {
  code: string;
  value: string;
}

export interface MarcControlField {
  tag: string;
  value: string;
}

export interface GndAlternative {
  id: string;
  label: string;
}

export interface DateAlternative {
  value: string;
  label: string;
}

export interface MarcDataField {
  tag: string;
  ind1: string;
  ind2: string;
  subfields: MarcSubfield[];
  gnd_alternatives?: GndAlternative[];
  date_alternatives?: DateAlternative[];
}

export interface ValidationResult {
  status: 'ok' | 'warning' | 'error';
  mandatory_missing: string[];
  individualization_count: number;
  group1_count: number;
  group2_count: number;
  group1_present: string[];
  group2_present: string[];
  warnings: string[];
}

export interface MarcRecord {
  qid: string;
  label: string;
  leader: string;
  controlfields: MarcControlField[];
  datafields: MarcDataField[];
  validation: ValidationResult;
}

export interface ConvertResponse {
  records: MarcRecord[];
  errors: { qid: string; error: string }[];
}

export interface StreamEvent {
  type: 'progress' | 'record' | 'error' | 'done';
  message?: string;
  record?: MarcRecord;
  qid?: string;
  error?: string;
}

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private baseUrl = '/api';

  constructor(private http: HttpClient, private ngZone: NgZone) {}


  convertQids(qids: string[], source: string = 'server'): Observable<ConvertResponse> {
    return this.http.post<ConvertResponse>(`${this.baseUrl}/convert`, { qids, source });
  }

  convertSingle(qid: string, source: string = 'server'): Observable<{ record?: MarcRecord; error?: string }> {
    return this.http.get<{ record?: MarcRecord; error?: string }>(`${this.baseUrl}/convert/${qid}?source=${source}`);
  }

  convertStream(qids: string[], source: string = 'server', field079q: string[] = ['d'], field667a: string = ''): Observable<StreamEvent> {
    return new Observable<StreamEvent>(observer => {
      const q = (field079q.length > 0 ? field079q : ['d']).join(',');
      const url = `${this.baseUrl}/convert/stream?qids=${qids.join(',')}&source=${source}&field079q=${encodeURIComponent(q)}&field667a=${encodeURIComponent(field667a)}`;
      const eventSource = new EventSource(url);
      eventSource.onmessage = (event) => {
        const data: StreamEvent = JSON.parse(event.data);
        this.ngZone.run(() => observer.next(data));
        if (data.type === 'done') {
          eventSource.close();
          this.ngZone.run(() => observer.complete());
        }
      };
      eventSource.onerror = () => {
        eventSource.close();
        this.ngZone.run(() => observer.error('SSE-Verbindung fehlgeschlagen'));
      };
      return () => eventSource.close();
    });
  }

  validateRecord(record: MarcRecord): Observable<ValidationResult> {
    return this.http.post<ValidationResult>(`${this.baseUrl}/convert/validate`, record);
  }

  exportRecords(records: MarcRecord[]): Observable<Blob> {
    return this.http.post(`${this.baseUrl}/convert/export`, { records }, {
      responseType: 'blob',
    });
  }
}
