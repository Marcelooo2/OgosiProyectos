import { CommonModule } from '@angular/common';
import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ProfileEvidence, Diagnostic, Mention } from './profile-evidence';
import { CatalogEditor } from './catalog-editor';

type View = 'inicio' | 'perfil' | 'diagnostico' | 'ruta' | 'seguimiento' | 'admin';
interface User { id: number; email: string; full_name: string; role: string; consent: boolean; }
interface ManagedAccount extends User { created_at: string; suspended: boolean; }
interface Requirement { competency_id: number; name: string; required_level: number; weight: number; }
interface JobProfile { id: number; name: string; description: string; seniority: string; requirements: Requirement[]; }
interface Resource { progress_id: number | null; resource_id: number; priority?: number; gap?: number; modality?: string; title: string; provider: string; url: string; type: string; competency: string; percent: number; completed: boolean; }


@Component({ selector: 'app-root', imports: [CommonModule, FormsModule, ProfileEvidence, CatalogEditor], templateUrl: './app.html', styleUrl: './app.scss' })
export class App {
  private readonly http = inject(HttpClient);
  readonly localPreview = ['localhost', '127.0.0.1'].includes(location.hostname) && location.port === '4200';
  readonly api = this.localPreview ? location.protocol + '//' + location.hostname + ':8000/api' : '/api';
  token = signal(localStorage.getItem('skillgrow_token') ?? '');
  user = signal<User | null>(JSON.parse(localStorage.getItem('skillgrow_user') ?? 'null'));
  view = signal<View>('inicio'); profiles = signal<JobProfile[]>([]); selectedProfile = signal<JobProfile | null>(null);
  assessment = signal<Diagnostic | null>(null); resources = signal<Resource[]>([]); detected = signal<Mention[]>([]);
  loading = signal(false); toast = signal(''); error = signal(''); isRegister = signal(false); menuOpen = signal(false);
  email = this.localPreview ? 'postulante@skillgrow.pe' : ''; password = this.localPreview ? 'Demo123!' : ''; fullName = ''; consent = false;
  suggestions = signal<Resource[]>([]);
  modality = '';
  biggestGap = computed(() => this.assessment()?.scores.filter(s => (s.gap ?? 0) > 0)
    .sort((a, b) => (b.priority ?? b.gap ?? 0) - (a.priority ?? a.gap ?? 0))[0]);
  completedCount = computed(() => this.resources().filter(r => r.completed).length);

  private ownerInvite = '';
  constructor() {
    const invite = new URLSearchParams(location.hash.slice(1)).get('activar');
    if (invite) {
      this.ownerInvite = invite;
      history.replaceState(null, '', location.pathname + location.search);
    }
    if (this.token()) this.bootstrap();
  }
  private headers() { return new HttpHeaders({ Authorization: `Bearer ${this.token()}` }); }
  private friendlyError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'No se pudo conectar con el servidor de SkillGrow.';
      const detail = error.error?.detail;
      return typeof detail === 'string' ? detail : 'Revisa los datos del formulario e inténtalo de nuevo.';
    }
    return 'Ocurrió un error inesperado.';
  }
  submitAuth() {
    this.loading.set(true); this.error.set('');
    const endpoint = this.isRegister() ? 'register' : 'login';
    const payload = this.isRegister() ? { email: this.email, password: this.password, full_name: this.fullName, consent: this.consent } : { email: this.email, password: this.password, renew_consent: this.consent };
    this.http.post<any>(`${this.api}/auth/${endpoint}`, payload).subscribe({
      next: data => { this.token.set(data.access_token); this.user.set(data.user); localStorage.setItem('skillgrow_token', data.access_token); localStorage.setItem('skillgrow_user', JSON.stringify(data.user)); this.loading.set(false); this.bootstrap(); },
      error: e => { this.error.set(this.friendlyError(e)); this.loading.set(false); }
    });
  }
  bootstrap() {
    if (this.ownerInvite) {
      this.http.post<User>(this.api + '/me/claim-owner', {code:this.ownerInvite}, {headers:this.headers()}).subscribe({
        next: account => {
          this.ownerInvite = '';
          this.user.set(account);
          localStorage.setItem('skillgrow_user', JSON.stringify(account));
          this.navigate('admin');
        }, error: e => this.error.set(this.friendlyError(e))
      });
    }
    this.loadProfiles(); this.loadProgress();
    this.http.get<Diagnostic>(`${this.api}/assessments/latest`, { headers: this.headers() }).subscribe({ next: value => { this.assessment.set(value); this.loadRecommendations(); }, error: () => void 0 });
  }
  revokeConsent() {
    if (!window.confirm('¿Retirar tu consentimiento y cerrar las sesiones de SkillGrow? Puedes reactivarlo al ingresar.')) return;
    this.http.post(this.api + '/me/consent/revoke', {}, {headers: this.headers()}).subscribe({
      next: () => { this.logout(); this.consent = false; },
      error: err => this.error.set(this.friendlyError(err))
    });
  }
  loadProfiles() {
    this.http.get<JobProfile[]>(`${this.api}/profiles`, { headers: this.headers() }).subscribe({ next: values => { this.profiles.set(values); if (values.length && !this.selectedProfile()) this.chooseProfile(values[0]); }, error: e => { if (e.status === 401) this.logout(); this.error.set(this.friendlyError(e)); } });
  }
  chooseProfile(profile: JobProfile) { this.selectedProfile.set(profile); }
  diagnosed(value: Diagnostic) {
    this.assessment.set(value); this.navigate('diagnostico'); this.loadRecommendations();
    this.toast.set(value.reused ? 'Se conserva el diagnóstico vigente. Responde un nuevo cuestionario para medir el cambio.' : 'Diagnóstico registrado.');
  }
  loadRecommendations() {
    const requestedToken = this.token();
    this.http.get<Resource[]>(this.api + '/route/suggestions', {
      headers: this.headers(), params: this.modality ? {modality: this.modality} : {}
    }).subscribe({next: values => { if (this.token() === requestedToken) this.suggestions.set(values); },
      error: e => { if (e.status !== 404) this.error.set(this.friendlyError(e)); }});
  }
  enroll(resource: Resource) {
    this.http.post(this.api + '/route/enroll/' + resource.resource_id, {}, {headers:this.headers()}).subscribe({
      next: () => { this.loadProgress(); this.loadRecommendations(); this.toast.set('Recurso añadido a tu ruta.'); },
      error: e => this.error.set(this.friendlyError(e))});
  }
  loadProgress() {
    const requestedToken = this.token();
    this.http.get<Resource[]>(this.api + '/progress', {headers:this.headers()}).subscribe({
      next: values => { if (this.token() === requestedToken) this.resources.set(values); }, error: e => this.error.set(this.friendlyError(e))});
  }
  updateProgress(item: Resource, percent: number | string) {
    this.http.patch<any>(`${this.api}/progress/${item.progress_id}`, { percent: Number(percent) }, { headers: this.headers() }).subscribe({ next: value => { this.resources.update(list => list.map(r => r.progress_id === item.progress_id ? { ...r, percent: value.percent, completed: value.completed } : r)); this.toast.set(value.notice); }, error: e => this.error.set(this.friendlyError(e)) });
  }
  accounts = signal<ManagedAccount[]>([]);
  moreAccounts = signal(false);
  accountOffset = 0;
  loadAccounts(offset = 0) {
    this.http.get<{users: ManagedAccount[]; more:boolean}>(this.api + '/admin/users',
      {headers:this.headers(), params:{offset}}).subscribe({
      next: page => {this.accountOffset = offset; this.accounts.set(page.users); this.moreAccounts.set(page.more);},
      error: e => this.error.set(this.friendlyError(e))
    });
  }
  changeAccess(id:number, email:string, suspended:boolean) {
    if (!window.confirm((suspended ? '¿Suspender el acceso de ' : '¿Reactivar el acceso de ') + email + '?')) return;
    this.http.patch(this.api + '/admin/users/' + id + '/access', {suspended}, {headers:this.headers()}).subscribe({
      next: () => this.loadAccounts(this.accountOffset), error: e => this.error.set(this.friendlyError(e))
    });
  }
  navigate(target: View) { if (target === 'admin') this.loadAccounts(); this.view.set(target); this.menuOpen.set(false); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  logout() { localStorage.removeItem('skillgrow_token'); localStorage.removeItem('skillgrow_user'); this.token.set(''); this.user.set(null); this.assessment.set(null); this.resources.set([]); this.suggestions.set([]); this.detected.set([]); this.selectedProfile.set(null); this.profiles.set([]); this.view.set('inicio'); this.consent = false; this.password = ''; this.toast.set(''); this.error.set(''); this.modality = ''; }
}

