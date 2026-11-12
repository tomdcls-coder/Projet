# repo-catchup — Mistral Workflow

Workflow Mistral de rattrapage GitHub : à partir d'un dépôt (`owner/name`) et d'une date, il liste les pull requests **fusionnées depuis cette date** et les pull requests **en attente de revue**, chacune avec un résumé d'une ligne et un lien, puis publie le récapitulatif dans la conversation Slack de l'utilisateur avec lui-même.

## Structure

- `apps/worker/src/worker/workflows/repo_catchup.py` — le workflow `repo-catchup` et ses activités
- `apps/worker/src/worker/entrypoints/worker.py` — entrypoint du worker (découverte automatique des workflows)
- `deploy/docker/Dockerfile.worker` — image des déploiements gérés Mistral Cloud
- `tests/` — tests unitaires des fonctions pures (parsing, digest)

## Entrées

| Champ | Type | Description |
|---|---|---|
| `repo` | `str` | Dépôt GitHub, format `owner/name` (URL github.com acceptée) |
| `since` | `str` | Date de départ, format `YYYY-MM-DD` |
| `slack_user_id` | `str \| null` | Optionnel : ID Slack de destination. Par défaut, résolu automatiquement (DM avec soi-même) |
| `max_per_section` | `int` | Optionnel : nombre max de PR par section (défaut : 20) |

## Exécution

```python
import os
from mistralai.client import Mistral

client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])

execution = client.workflows.execute_workflow(
    workflow_identifier="repo-catchup",
    input={"repo": "owner/name", "since": "2025-01-15"},
    wait_for_result=True,
)
print(execution.model_dump_json(indent=2))
```

Le workflow s'exécute `on_behalf_of` : il utilise les identifiants GitHub et Slack de l'utilisateur déclencheur (connecteurs `github_app` et `slack`, résolus par la plateforme — aucun secret dans le code).

## Développement local

```bash
uv sync
uv run pytest
PYTHONPATH=apps/worker/src uv run python -m worker.entrypoints.worker  # worker local
```

## Déploiement géré

Le déploiement Mistral Cloud build l'image depuis `deploy/docker/Dockerfile.worker` (contexte : racine du dépôt).
