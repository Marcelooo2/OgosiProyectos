import { Component, EventEmitter, Input, OnInit, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { forkJoin } from 'rxjs';

type Kind = 'competencies' | 'profiles' | 'resources';
interface Requirement { competency_id: number; required_level: number; weight: number; }
interface Entry {
  id: number; archived: boolean; name?: string; category?: string; aliases?: string[]; description?: string;
  seniority?: string; requirements?: Requirement[]; title?: string; provider?: string; url?: string;
  resource_type?: string; competency_id?: number; modality?: string; hours?: number; entry_level?: number;
  starts_on?: string; ends_on?: string;
}
interface Catalog extends Record<Kind, Entry[]> { version: number; parameters: {semantic_threshold: number}; }
interface Revision { version: number; kind: string; item_id: number; action: string; date: string; }

@Component({selector: 'sg-catalog', imports: [FormsModule], templateUrl: './catalog-editor.html',
  styles: [`section {padding:1.2rem;margin:1rem 0;border:1px solid #ddd9ea;border-radius:1rem;background:white}
    label {display:grid;gap:.3rem;margin:.65rem 0} input,textarea,select,button {font:inherit;padding:.45rem .65rem;max-width:100%}
    button {margin:.25rem;cursor:pointer} ul {list-style:none;padding:0} li {padding:.35rem 0;border-bottom:1px solid #eee}
    .fields {display:flex;flex-wrap:wrap;gap:.7rem;align-items:end} .fields label {flex:1;min-width:7rem} .message {color:#4d3289}`]
})
export class CatalogEditor implements OnInit {
  @Input({required:true}) api!: string;
  @Input({required:true}) token!: string;
  @Output() published = new EventEmitter<void>();
  private http = inject(HttpClient);
  kind: Kind = 'competencies';
  labels: Record<Kind,string> = {competencies:'Competencias',profiles:'Perfiles',resources:'Recursos'};
  kinds: Kind[] = ['competencies','profiles','resources'];
  catalog = signal<Catalog | null>(null);
  history = signal<Revision[]>([]);
  message = signal('');
  busy = signal(false);
  entry: Partial<Entry> = {};
  aliases = '';
  requirements: Requirement[] = [];
  threshold = .92;
  private get base() { return this.api + '/admin/catalog'; }
  private get options() { return { headers: new HttpHeaders({Authorization:'Bearer ' + this.token}) }; }
  ngOnInit() { this.refresh(); this.edit(); }
  refresh() {
    forkJoin({catalog:this.http.get<Catalog>(this.base,this.options), history:this.http.get<Revision[]>(this.base+'/history',this.options)})
      .subscribe({next:data => {this.catalog.set(data.catalog); this.history.set(data.history); this.threshold=data.catalog.parameters.semantic_threshold;},
        error:() => this.message.set('No se pudieron cargar los catálogos.')});
  }
  select(kind: Kind) { this.kind=kind; this.edit(); }
  edit(item?: Entry) {
    this.entry = item ? {...item} : {category:'Técnica',seniority:'Junior',resource_type:'Curso',modality:'En línea',entry_level:0};
    this.aliases = (item?.aliases || []).join(', ');
    this.requirements = (item?.requirements || []).map(r => ({competency_id:r.competency_id,required_level:r.required_level,weight:r.weight}));
    this.message.set('');
  }
  addRequirement() {
    const skill = this.catalog()?.competencies.find(s=>!s.archived && !this.requirements.some(r=>r.competency_id===s.id));
    if (skill) this.requirements.push({competency_id:skill.id,required_level:3,weight:1});
  }
  publish() {
    const e=this.entry;
    const payload = this.kind === 'competencies' ? {name:e.name,category:e.category,aliases:this.aliases.split(',').map(a=>a.trim()).filter(Boolean)}
      : this.kind === 'profiles' ? {name:e.name,description:e.description,seniority:e.seniority,requirements:this.requirements}
      : {title:e.title,competency_id:e.competency_id,provider:e.provider,url:e.url,resource_type:e.resource_type,modality:e.modality,
          hours:e.hours || null,entry_level:e.entry_level ?? 0,starts_on:e.starts_on || null,ends_on:e.ends_on || null};
    this.mutate(e.id?'PUT':'POST', '/'+this.kind+(e.id?'/'+e.id:''), payload);
  }
  status(e:Entry) { this.mutate('PATCH','/'+this.kind+'/'+e.id+'/status',{archived:!e.archived}); }
  parameters() { this.mutate('PUT','/parameters',{semantic_threshold:this.threshold}); }
  private mutate(method:string, path:string, body:unknown) {
    this.busy.set(true);
    this.http.request(method,this.base+path,{...this.options,body}).subscribe({
      next:()=>{this.busy.set(false);this.edit();this.refresh();this.message.set('Publicación registrada.');this.published.emit();},
      error:err=>{this.busy.set(false); const d=err.error?.detail;this.message.set(typeof d==='string'?d:'Revisa los campos y los requisitos de la ficha.');}
    });
  }
}
