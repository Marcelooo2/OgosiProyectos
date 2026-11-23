"""Versioned academic question bank. Correct choices are served only to the scorer."""
from dataclasses import dataclass
from collections import defaultdict


@dataclass(frozen=True)
class Question:
    key: str
    skill: str
    prompt: str
    choices: tuple[str, str, str]
    correct: int


BANK_VERSION = "es-1"
BANK = (
    Question("py-1", "Python", "Dos claves deben apuntar a valores distintos. ¿Qué estructura usarías?", ("Una lista de claves", "Un diccionario", "Una cadena"), 1),
    Question("py-2", "Python", "¿Qué garantiza 'with open(...) as archivo' al salir del bloque?", ("Que el archivo se cierre", "Que el archivo sea público", "Que se elimine el archivo"), 0),
    Question("fa-1", "FastAPI", "¿Qué suele hacer FastAPI si el cuerpo recibido incumple el esquema Pydantic?", ("Guardar una copia válida", "Cambiar el método HTTP", "Devolver un error de validación"), 2),
    Question("fa-2", "FastAPI", "¿Cómo compartes la obtención de una sesión de base de datos entre rutas?", ("Con una variable en el navegador", "Con una dependencia declarada mediante Depends", "Con una plantilla HTML"), 1),
    Question("sq-1", "SQL", "Deseas conservar los clientes aunque no tengan pedidos. ¿Qué operación une pedidos a clientes?", ("LEFT JOIN desde clientes", "INNER JOIN desde pedidos", "INTERSECT entre nombres"), 0),
    Question("sq-2", "SQL", "Una transferencia bancaria modifica dos saldos. ¿Cómo evitas confirmar solo uno?", ("Usando un índice por importe", "Ordenando ambas consultas", "Ejecutando ambas operaciones en una transacción"), 2),
    Question("gi-1", "Git", "¿Qué comando muestra los cambios de archivos rastreados que todavía no has preparado?", ("git fetch", "git diff", "git tag"), 1),
    Question("gi-2", "Git", "Tras resolver un conflicto de fusión, ¿cómo marcas el archivo como resuelto?", ("git add seguido de la ruta del archivo", "git remote seguido de la ruta", "git stash list"), 0),
    Question("an-1", "Angular", "¿En qué lugar defines la vista declarativa de un componente?", ("En el archivo de bloqueo de dependencias", "En la tabla de rutas del servidor", "En su plantilla"), 2),
    Question("an-2", "Angular", "¿Qué herramienta de Angular se configura con provideHttpClient para llamar a una API?", ("RouterOutlet", "HttpClient", "DatePipe"), 1),
    Question("re-1", "REST API", "¿Qué código corresponde a la creación satisfactoria de un recurso?", ("201", "404", "503"), 0),
    Question("re-2", "REST API", "Una petición obtiene un recurso sin modificarlo. ¿Qué método es apropiado?", ("PATCH", "DELETE", "GET"), 2),
    Question("do-1", "Docker", "¿Qué artefacto usa Docker como base inmutable para crear contenedores?", ("Un puerto", "Una imagen", "Una sesión"), 1),
    Question("do-2", "Docker", "¿Dónde conservas los datos de PostgreSQL para poder recrear el contenedor?", ("En un volumen persistente", "En la salida estándar", "En un proceso temporal"), 0),
    Question("co-1", "Comunicación", "Un requisito es ambiguo. ¿Qué harías antes de programarlo?", ("Suponer el caso más sencillo", "Posponer cualquier conversación", "Pedir un ejemplo y confirmar el criterio de aceptación"), 2),
    Question("co-2", "Comunicación", "Debes informar de un retraso. ¿Qué mensaje permite al equipo actuar?", ("La tarea va mal", "Describe el impacto, la causa conocida y una opción de recuperación", "Todo saldrá bien"), 1),
    Question("te-1", "Trabajo en equipo", "Dos integrantes dependen de la misma tarea bloqueada. ¿Qué acción facilita colaborar?", ("Acordar responsable, ayuda y siguiente revisión", "Mantener dos cronogramas privados", "Esperar al cierre del proyecto"), 0),
    Question("te-2", "Trabajo en equipo", "Tu colega discrepa en una revisión de código. ¿Cómo decides con el equipo?", ("Apruebas sin revisar", "Escoges la opinión más reciente", "Contrastan criterios técnicos y documentan el acuerdo"), 2),
    Question("rp-1", "Resolución de problemas", "Una aplicación falla solo con ciertos datos. ¿Cuál es un primer paso comprobable?", ("Añadir memoria sin medir", "Aislar una entrada mínima que reproduce el fallo", "Cambiar todos los módulos"), 1),
    Question("rp-2", "Resolución de problemas", "Después de corregir un fallo, ¿qué evidencia respalda la corrección?", ("La prueba que fallaba pasa y los casos relacionados siguen funcionando", "Cambió la longitud del archivo", "El equipo cambió de herramienta"), 0),
)


def profile_questions(requirements):
    by_name = {r.competency.name.casefold(): r.competency_id for r in requirements}
    return [(q, by_name[q.skill.casefold()]) for q in BANK if q.skill.casefold() in by_name]


def public_items(questions):
    return [{"id": q.key, "skill": q.skill, "prompt": q.prompt, "choices": q.choices}
            for q, _ in questions]


def grade(questions, answers):
    if not questions or set(answers) != {q.key for q, _ in questions}:
        raise ValueError("Responde todas las preguntas de la versión actual del perfil.")
    by_skill = defaultdict(list)
    for question, competency_id in questions:
        selected = answers[question.key]
        if type(selected) is not int or not 0 <= selected < len(question.choices):
            raise ValueError("Hay una alternativa inválida.")
        by_skill[str(competency_id)].append(selected == question.correct)
    return {key: round(100 * sum(hits) / len(hits), 1) for key, hits in by_skill.items()}


def diagnostic_score(requirement, sources):
    # API levels are percentages of the 0–5 scale, so 20 points equal one level.
    declared, verified = sources["self"], sources["quiz"]
    coverage = (10 if sources["cv"] is not None else 0) + (20 if declared is not None else 0) + (70 if verified is not None else 0)
    required = 20 * requirement.required_level
    evidence = declared if verified is None else verified
    if declared is not None and verified is not None:
        evidence = .30 * declared + .70 * verified
    ratio = max(0, required - evidence) / required if evidence is not None else None
    if evidence is None:
        label = "No evidenciada"
    elif verified is None:
        label = "Declarada (provisional)"
    elif ratio == 0:
        label = "Fuerte"
    elif ratio < .35:
        label = "Moderada"
    else:
        label = "Faltante"
    return {"competency_id": requirement.competency_id, "name": requirement.competency.name,
            "category": requirement.competency.category, "required": required, "weight": requirement.weight,
            "evidence": evidence, "gap": round(100 * ratio, 4) if ratio is not None else None,
            "priority": requirement.weight * ratio if ratio is not None else None,
            "status": label, "sources": sources, "evidence_coverage": coverage, "method": "5.5.2-v1"}


def summarize(scores):
    all_weights = sum(x["weight"] for x in scores)
    evaluable = [x for x in scores if x["gap"] is not None]
    covered = sum(x["weight"] for x in evaluable)
    coverage = 100 * covered / all_weights if all_weights else 0
    gap = 100 * sum(x["priority"] for x in evaluable) / covered if covered else None
    return {"coverage": round(coverage, 2), "partial": coverage < 70,
            "global_gap": round(gap, 2) if gap is not None else None,
            "overall_fit": round(100 - gap, 2) if gap is not None else None}
