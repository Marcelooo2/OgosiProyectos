import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Component, EventEmitter, Input, OnChanges, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';

interface Profile { id: number; name: string; requirements: { competency_id: number; name: string }[]; }
export interface Mention { competency_id: number; name: string; source: string; confirmed?: boolean; }
interface Cv { evidence_id: number | null; filename: string | null; detected: Mention[]; notice?: string; }
interface Attempt { id: number; created_at: string; scores: Record<string, number>; }
interface Sheet {
  version: string; items: { id: string; skill: string; prompt: string; choices: string[] }[];
  uncovered: string[]; self_scores: Record<string, number>; latest: Attempt | null;
}
export interface Diagnostic {
  id: number; profile_id?: number; profile: string; overall_fit: number | null; created_at: string;
  global_gap?: number | null; partial?: boolean; coverage?: number; method?: string;
  reused?: boolean; catalog_version?: number;
  scores: {
    competency_id: number; name: string; category: string; required: number; evidence: number | null;
    gap: number | null; status: string; priority?: number; evidence_coverage?: number; cv_source?: string;
    verified_at?: string; sources?: { cv: number | null; self: number | null; quiz: number | null };
  }[];
}

@Component({
  selector: 'sg-evidence', standalone: true, imports: [FormsModule],
  templateUrl: './profile-evidence.html',
  styles: [`
    :host {display:block} section {background:white;border:1px solid #dedce8;border-radius:1rem;padding:1.4rem;margin:1rem 0}
    h3 {margin:0 0 .65rem;font-size:1.2rem} p {line-height:1.55;color:#565266} fieldset {border:0;margin:1.3rem 0;padding:0}
    legend {font-weight:700;margin-bottom:.45rem} label {display:flex;align-items:center;gap:.6rem;margin:.5rem 0}
    input[type=number] {width:5.5rem;border:1px solid #aaa4b9;border-radius:.45rem;padding:.5rem;font:inherit}
    button {font:inherit;font-weight:650;cursor:pointer;border:1px solid #6053b1;background:#fff;color:#45358c;border-radius:.6rem;padding:.65rem 1rem;margin:.45rem .45rem .2rem 0}
    button.primary {background:#5646bc;color:white} button:disabled {opacity:.55;cursor:not-allowed}
    label.level {display:grid;grid-template-columns:minmax(10rem,1fr) 6rem;max-width:32rem;margin:.8rem 0}
    .feedback {padding:.65rem .85rem;background:#f1efff;border-radius:.55rem} .error {background:#fff0ed;color:#942f25}
    small {color:#635c72} input[type=file]{max-width:100%}
  `]
})
export class ProfileEvidence implements OnChanges {
  private readonly http = inject(HttpClient);
  @Input({required: true}) api!: string;
  @Input({required: true}) token!: string;
  @Input({required: true}) profile!: Profile;
  @Output() diagnosed = new EventEmitter<Diagnostic>();
  @Output() mentionsChanged = new EventEmitter<Mention[]>();
  sheet = signal<Sheet | null>(null);
  cv = signal<Cv | null>(null);
  busy = signal(false);
  error = signal('');
  feedback = signal('');
  levels: Record<string, number | null> = {};
  answers: Record<string, number> = {};
  private generation = 0;
  private get options() { return {headers: new HttpHeaders({Authorization: 'Bearer ' + this.token})}; }
  private get route() { return this.api + '/profiles/' + this.profile.id; }

  ngOnChanges() {
    const generation = ++this.generation;
    this.sheet.set(null); this.cv.set(null); this.levels = {}; this.answers = {};
    this.error.set(''); this.feedback.set(''); this.busy.set(true);
    forkJoin({
      sheet: this.http.get<Sheet>(this.route + '/questionnaire', this.options),
      cv: this.http.get<Cv>(this.api + '/evidence/cv/latest', this.options)
    }).subscribe({
      next: state => {
        if (generation !== this.generation) return;
        for (const [name, value] of Object.entries(state.sheet.self_scores)) this.levels[name] = value / 20;
        this.sheet.set(state.sheet); this.setCv(state.cv); this.busy.set(false);
      }, error: err => { if (generation === this.generation) this.fail(err); }
    });
  }
  private setCv(value: Cv) { this.cv.set(value); this.mentionsChanged.emit(value.detected); }
  private fail(err: HttpErrorResponse) {
    const detail = err.error?.detail;
    this.error.set(typeof detail === 'string' ? detail : 'Revisa los datos e inténtalo de nuevo.');
    this.busy.set(false);
  }
  private begin() { this.busy.set(true); this.error.set(''); this.feedback.set(''); return this.generation; }
  private save<T>(request: import('rxjs').Observable<T>, done: (value: T) => void) {
    const generation = this.begin();
    request.subscribe({
      next: value => { if (generation === this.generation) { this.busy.set(false); done(value); } },
      error: err => { if (generation === this.generation) this.fail(err); }
    });
  }
  upload(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 5_000_000) { this.error.set('El CV supera los 5 MB.'); return; }
    const form = new FormData(); form.append('file', file); input.value = '';
    this.save(this.http.post<Cv>(this.api + '/evidence/cv', form, this.options), value => {
      this.setCv(value); this.feedback.set(value.notice ?? 'CV procesado.');
    });
  }
  confirmCv() {
    const cv = this.cv();
    if (!cv?.evidence_id) return;
    const confirmed_ids = cv.detected.filter(item => item.confirmed).map(item => item.competency_id);
    this.save(this.http.put<Pick<Cv,'detected'>>(this.api + '/evidence/cv/' + cv.evidence_id + '/confirmation', {confirmed_ids}, this.options), value => {
      this.setCv({...cv, detected:value.detected}); this.feedback.set('Confirmación guardada.');
    });
  }
  private declaration() {
    return Object.fromEntries(Object.entries(this.levels)
      .filter((item): item is [string, number] => item[1] != null).map(([name, value]) => [name, value * 20]));
  }
  validLevels() { return Object.values(this.levels).every(value => value == null || (Number.isFinite(value) && value >= 0 && value <= 5)); }
  saveLevels() {
    this.save(this.http.put(this.route + '/declaration', {self_scores: this.declaration()}, this.options), () => this.feedback.set('Autodeclaración guardada.'));
  }
  answered() { return Object.keys(this.answers).length; }
  verify() {
    const sheet = this.sheet();
    if (!sheet) return;
    this.save(this.http.post<Attempt>(this.route + '/questionnaire', {version:sheet.version, answers:this.answers}, this.options), result => {
      this.sheet.set({...sheet, latest:result}); this.answers = {}; this.feedback.set('Intento corregido. Ya puedes calcular el diagnóstico con esta evidencia.');
    });
  }
  diagnose() {
    this.save(this.http.post<Diagnostic>(this.api + '/assessments', {profile_id:this.profile.id, self_scores:this.declaration()}, this.options), value => this.diagnosed.emit(value));
  }
}
