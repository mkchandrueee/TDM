import json
from time import perf_counter
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tigr.config import get_settings
from tigr.scenario_schemas import MappingField


def _synthetic_json_type(data_type: str) -> str:
    kind = data_type.casefold()
    if any(name in kind for name in ("int", "serial", "bigint")):
        return "integer"
    if any(name in kind for name in ("numeric", "decimal", "float", "double", "real")):
        return "number"
    if "bool" in kind:
        return "boolean"
    if "array" in kind:
        return "array"
    if "json" in kind or "object" in kind:
        return "object"
    return "string"


def _decode_synthetic_rows(content: object, expected_fields: list[str]) -> list[dict] | None:
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        decoder = json.JSONDecoder()
        parsed = None
        for index, char in enumerate(content):
            if char not in "[{":
                continue
            try:
                parsed, _ = decoder.raw_decode(content[index:])
                break
            except json.JSONDecodeError:
                continue
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        rows = next((parsed.get(key) for key in ("rows", "records", "data") if parsed.get(key) is not None), None)
        if rows is None and set(expected_fields).issubset(parsed):
            rows = [parsed]
        if isinstance(rows, str):
            try:
                rows = json.loads(rows)
            except json.JSONDecodeError:
                return None
        if isinstance(rows, dict):
            rows = [rows]
    else:
        return None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    return rows


class ScenarioAssistant(Protocol):
    """Provider-neutral schema assistant; inputs contain requirements and metadata only."""

    provider_name: str
    model_version: str
    prompt_version: str

    def suggest_mapping(self, requirement: str, fields: list[dict]) -> list[MappingField]: ...
    def generate_schema(self, requirement: str, mode: str, structure: list[dict], expected_states: list[str] | None = None, exclusions: list[str] | None = None, feedback: str = "", previous_response: dict | None = None, selected_source_objects: list[str] | None = None, previous_intent: dict | None = None, on_stage=None) -> tuple[list[dict], list[dict], list[dict], list[str], str, dict, list[dict]]: ...
    def generate_query(self, requirement: str, structure: list[dict], fields: list[dict], relationships: list[dict], filters: list[dict], row_limit: int) -> tuple[str, str]: ...
    def generate_synthetic_rows(self, requirement: str, fields: list[dict], relationships: list[dict], row_count: int, sql_query: str | None = None) -> tuple[list[dict], str]: ...
    def review_synthetic_sql(self, query: str, available_fields: list[dict]) -> tuple[str, str, str | None, str | None, list[dict]]: ...


class OllamaScenarioAssistant:
    provider_name = "Ollama"
    prompt_version = "scenario-schema-v12"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.model_version = self.settings.ollama_model

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = self.settings.ollama_url.rstrip("/") + path
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
        try:
            with urlopen(request, timeout=self.settings.ollama_request_timeout_seconds if payload else 5) as response:
                result = json.loads(response.read())
        except TimeoutError as exc:
            raise RuntimeError("Gemma took too long to finish the schema suggestion. Retry, or use a shorter requirement.") from exc
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Local Ollama is unavailable. Start Ollama and install a Gemma model.") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Ollama returned an invalid response")
        return result

    def _model(self) -> str:
        configured = self.settings.ollama_model.strip()
        if configured and configured.casefold() != "auto":
            return configured
        tags = self._request("/api/tags").get("models", [])
        gemma_models = [item for item in tags if isinstance(item, dict) and "gemma" in str(item.get("name", "")).casefold()]
        if not gemma_models:
            raise RuntimeError("No local Gemma model is installed in Ollama. Install one, then retry schema generation.")
        gemma_models.sort(key=lambda item: str(item.get("modified_at", "")))
        return str(gemma_models[-1]["name"])

    def suggest_mapping(self, requirement: str, fields: list[dict]) -> list[MappingField]:
        # Suggestions are produced on demand by generate_schema; avoid invoking an LLM
        # during routine plan reads.
        return []

    def generate_schema(self, requirement: str, mode: str, structure: list[dict], expected_states: list[str] | None = None, exclusions: list[str] | None = None, feedback: str = "", previous_response: dict | None = None, selected_source_objects: list[str] | None = None, previous_intent: dict | None = None, on_stage=None) -> tuple[list[dict], list[dict], list[dict], list[str], str, dict, list[dict]]:
        if on_stage:
            on_stage("resolving_model")
        model = self._model()
        if on_stage:
            on_stage("model_ready")
        # Stage one captures the user's meaning before the model sees the source
        # catalog. This keeps requirement interpretation separate from schema
        # grounding and makes every criterion auditable in the UI.
        intent_format = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "output_grain": {"type": "string"},
                "entities": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "purpose": {"type": "string"},
                }, "required": ["name", "purpose"], "additionalProperties": False}},
                "criteria": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "string"}, "requirement": {"type": "string"},
                    "subject": {"type": "string"}, "quantifier": {"type": "string"},
                }, "required": ["id", "requirement", "subject", "quantifier"], "additionalProperties": False}},
                "exclusions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "output_grain", "entities", "criteria", "exclusions"],
            "additionalProperties": False,
        }
        intent_prompt = {
            "task": "Convert this test-data request into a concise, source-independent intent outline.",
            "requirement": requirement,
            "expected_states": expected_states or [],
            "exclusions": exclusions or [],
            "previous_suggestions": previous_response,
            "user_corrections": feedback.strip(),
            "rules": [
                "Do not choose database tables or fields in this stage.",
                "Treat conversational feedback as changes to the prior request and preserve the original intent unless feedback changes it.",
                "List each distinct entity and each inclusion, exclusion, state, count, or relationship condition as a criterion with a stable short id.",
                "Capture quantifiers precisely, including at least one, every, none, one row per, and optional relationships.",
                "Use user wording for business concepts; do not assume a business concept is its own entity or field.",
                "Keep the outline complete but concise. Do not include database values or personal data.",
            ],
        }
        if previous_intent and isinstance(previous_intent.get("criteria"), list) and isinstance(previous_intent.get("entities"), list):
            intent = previous_intent
            if on_stage:
                on_stage("schema_grounding", reused_requirement_outline=True)
        else:
            if on_stage:
                on_stage("requirement_interpretation")
            intent_started = perf_counter()
            intent_result = self._request("/api/chat", {
                "model": model, "stream": False, "format": intent_format,
                "options": {"temperature": 0.1, "num_predict": 1200, "num_ctx": 8192},
                "messages": [{"role": "user", "content": json.dumps(intent_prompt)}],
            })
            if intent_result.get("done_reason") == "length":
                raise RuntimeError("Gemma stopped while structuring the requirement. Retry with a shorter requirement.")
            try:
                intent = json.loads(intent_result.get("message", {}).get("content"))
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Gemma did not return a valid requirement outline") from exc
            if not isinstance(intent, dict) or not isinstance(intent.get("criteria"), list) or not isinstance(intent.get("entities"), list):
                raise RuntimeError("Gemma returned an incomplete requirement outline")
            if on_stage:
                on_stage("requirement_interpretation_complete", duration_ms=int((perf_counter() - intent_started) * 1000))

        if on_stage:
            on_stage("schema_grounding")
        grounding_started = perf_counter()

        # Only send schema evidence needed to identify and validate mappings. Some
        # connectors attach display and preview attributes that needlessly inflate
        # the Ollama prompt; retain field types, keys, constraints, and FK edges.
        compact_structure = []
        field_keys = ("name", "type", "data_type", "primary_key", "nullable", "default", "unique", "enum_values", "check_constraints")
        for obj in structure:
            compact_structure.append({
                "name": obj.get("name"),
                "fields": [{key: field.get(key) for key in field_keys if key in field} for field in obj.get("fields", []) if isinstance(field, dict)],
                "foreign_keys": obj.get("foreign_keys", []),
                "unique_constraints": obj.get("unique_constraints", []),
                "check_constraints": obj.get("check_constraints", []),
            })

        response_format = {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array", "maxItems": 48,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "source_object": {"type": ["string", "null"]},
                            "source_field": {"type": "string"},
                            "target_field": {"type": "string"},
                            "data_type": {"type": "string"},
                            "primary_key": {"type": "boolean"},
                            "required": {"type": "boolean"},
                            "confidence": {"type": ["number", "null"]},
                            "rationale": {"type": ["string", "null"], "maxLength": 80},
                        },
                        "required": ["source_object", "source_field", "target_field", "data_type", "primary_key", "required", "confidence", "rationale"],
                    },
                },
                "source_joins": {
                    "type": "array", "maxItems": 20,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "left_object": {"type": "string"}, "left_field": {"type": "string"},
                            "right_object": {"type": "string"}, "right_field": {"type": "string"},
                            "confidence": {"type": ["number", "null"]},
                            "rationale": {"type": ["string", "null"], "maxLength": 80},
                        },
                        "required": ["left_object", "left_field", "right_object", "right_field", "confidence", "rationale"],
                    },
                },
                "filters": {
                    "type": "array", "maxItems": 30,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "field": {"type": "string"},
                            "source_object": {"type": ["string", "null"]},
                            "source_field": {"type": ["string", "null"]},
                            "operator": {"type": "string", "enum": ["eq", "ne", "lt", "lte", "gt", "gte", "between", "in", "is_null"]},
                            "value": {}, "confidence": {"type": ["number", "null"]},
                            "rationale": {"type": ["string", "null"], "maxLength": 80},
                        },
                        "required": ["field", "source_object", "source_field", "operator", "value", "confidence", "rationale"],
                    },
                },
                "criteria_coverage": {"type": "array", "maxItems": 40, "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "criterion_id": {"type": "string"}, "covered": {"type": "boolean"},
                        "source_object": {"type": ["string", "null"]}, "source_field": {"type": ["string", "null"]},
                        "explanation": {"type": "string", "maxLength": 240},
                    }, "required": ["criterion_id", "covered", "source_object", "source_field", "explanation"],
                }},
                "notes": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 180}},
            },
            "required": ["fields", "source_joins", "filters", "criteria_coverage", "notes"],
            "additionalProperties": False,
        }
        prompt = {
            "task": "Ground the approved requirement outline in the supplied schema catalog and return a reviewable mapping with explicit criterion coverage.",
            "requirement_outline": intent,
            "previous_generation": previous_response,
            "user_feedback": feedback.strip(),
            "generation_mode": mode,
            "user_selected_source_objects": selected_source_objects or [],
            "approved_source_metadata": compact_structure,
            "rules": [
                "Return only the JSON object described by the output schema; no prose or SQL.",
                "Interpret user_feedback as natural-language instructions, corrections, suggestions, or observations. Infer the intended change even when it is phrased as a question or conversational request; do not require exact schema names or a structured command.",
                "Treat the original requirement and expected_states/exclusions as the base intent, then apply user_feedback to revise the previous_generation. Feedback may add or remove objects, request additional relevant fields, correct a mapping, or ask you to reconsider an earlier choice.",
                "When previous_generation is present, make every change requested by user_feedback and return the complete revised response. Apply clear feedback as required work, not an optional preference; discard prior suggestions that are extraneous or attach a criterion to the wrong object.",
                "For a feedback request for more fields, include additional fields that best support the stated test purpose. Prioritize coverage of required objects and criteria over optional fields from the previous response.",
                "If user_feedback asks to include or add an object/table and it exists in approved_source_metadata, it must appear in fields with its primary key and the field needed for the requested output or criterion, plus any needed relationship keys. An object listed only in source_joins does not satisfy this requirement. If unavailable or disconnected in approved metadata, state that in notes.",
                "If feedback is ambiguous, make the best schema-supported interpretation and state any remaining uncertainty briefly in notes. If a request conflicts with exclusions or approved metadata, explain the limitation in notes.",
                "For source-backed mode, use exact object, field, and type values from approved_source_metadata; never invent or relocate a field.",
                "For source-backed mode, use only user_selected_source_objects. Do not suggest fields, joins, or filters from any other source object.",
                "Use primary_key, nullable, default, unique_constraints, check_constraints, and foreign_keys as structural evidence. A filter value must come from the requirement or a declared check/enum constraint; do not infer that a value exists in source records.",
                "Suggest only critical fields: requested output attributes, fields implementing each criterion, primary keys for required objects, and relationship keys needed to connect them. Do not impose a small global field limit; keep the mapping focused on the requirement outline.",
                "Return one criteria_coverage entry for every criterion id in requirement_outline. Mark covered true only when a schema field or explicit supported exclusion implements it. If no field can represent it, mark false and explain the gap; do not guess.",
                "Before returning JSON, check each entity and criterion from requirement_outline against the schema. Do not omit an entity because it was absent from previous_generation.",
                "An object used only in source_joins is not covered when its fields supply requested data or a criterion. Include its primary key, criterion/output field, and relationship fields in fields as applicable.",
                "Attach each criterion to the entity it describes. If multiple objects contain a same-named field, use the field from the object described by the requirement, return distinct output names, and create separate filters rather than merging their values.",
                "Use supplied foreign_keys as authoritative relationship evidence. For each required object, return the supported relationship path to the other required objects, using exact object and field names from metadata; use an unambiguous shared key only when no foreign key is supplied.",
                "For every required source object, include its primary key when approved metadata marks one. Include all fields needed to connect the required objects, even if a relationship field is not part of the requested output.",
                "Set primary_key true only when approved metadata marks that exact source field as a primary key; otherwise false.",
                "Give concise, descriptive output names when different objects have same-named fields. Reference those output names in filters.",
                "Suggest only filters that implement explicit criteria. Each filter must identify both the source_object and source_field from the catalog; field is the matching output target_field when one is included, otherwise a concise qualified output name. Multiple alternative values for one field use one IN filter with a JSON array of scalar values; never combine values belonging to different fields. Filters are combined with AND.",
                "Do not claim an unsupported concept is represented by a related field; briefly explain metadata limitations in notes.",
                "Keep rationales brief. Estimate confidence from 0 to 1 or use null when uncertain. Never include record values.",
                "For synthetic mode, create useful fields with source_object null and source_field equal to target_field.",
            ],
        }
        result = self._request("/api/chat", {
            "model": model,
            "stream": False,
            "format": response_format,
            "options": {"temperature": 0.1, "num_predict": 6144, "num_ctx": 16384},
            "messages": [{"role": "user", "content": json.dumps(prompt)}],
        })
        if result.get("done_reason") == "length":
            raise RuntimeError("Gemma stopped before completing the schema response. Retry with a shorter requirement or fewer requested entities.")
        response = result.get("message", {}).get("content")
        try:
            parsed = json.loads(response)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama did not return a valid schema mapping") from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("fields"), list):
            raise RuntimeError("Ollama response must contain a fields list")
        if on_stage:
            on_stage("schema_grounding_complete", duration_ms=int((perf_counter() - grounding_started) * 1000))
            on_stage("validating_suggestions")
        validation_started = perf_counter()
        raw_fields = [item for item in parsed["fields"] if isinstance(item, dict)]
        fields = raw_fields if mode == "source_backed" else []
        if mode == "synthetic":
            for item in raw_fields:
                try:
                    fields.append(MappingField.model_validate(item).model_dump(mode="json"))
                except Exception:
                    continue
        if not fields:
            raise RuntimeError("Gemma did not return usable field suggestions. Review the requirement and retry.")
        # Validate object/field references against the approved metadata. This is
        # schema integrity checking only; semantic selection remains Gemma's.
        approved = {str(obj.get("name")): {str(field.get("name")): field for field in obj.get("fields", []) if isinstance(field, dict)} for obj in structure}
        if mode == "source_backed":
            valid_fields = []
            for item in fields:
                object_name, field_name = item.get("source_object"), item.get("source_field")
                field_meta = approved.get(str(object_name), {}).get(str(field_name))
                if field_meta is None:
                    continue
                item["data_type"] = str(field_meta.get("type") or item.get("data_type") or "TEXT")
                item["primary_key"] = bool(field_meta.get("primary_key", False))
                valid_fields.append(item)
            fields = valid_fields
            if not fields:
                raise RuntimeError("Gemma did not map any fields to the approved source schema. Review the requirement and schema metadata.")
        joins = parsed.get("source_joins", [])
        if mode == "source_backed":
            approved_relationships = set()
            for owner, obj in ((str(obj.get("name")), obj) for obj in structure if isinstance(obj, dict)):
                for fk in obj.get("foreign_keys", []):
                    if isinstance(fk, dict) and fk.get("field") and fk.get("references_object") and fk.get("references_field"):
                        approved_relationships.add((owner, str(fk["field"]), str(fk["references_object"]), str(fk["references_field"])))
            valid_joins = []
            for join in joins:
                if not isinstance(join, dict):
                    continue
                left = (join.get("left_object"), join.get("left_field"))
                right = (join.get("right_object"), join.get("right_field"))
                exact_relationship = (
                    (str(left[0]), str(left[1]), str(right[0]), str(right[1])) in approved_relationships
                    or (str(right[0]), str(right[1]), str(left[0]), str(left[1])) in approved_relationships
                )
                pair_has_declared_relationship = any(
                    {edge[0], edge[2]} == {str(left[0]), str(right[0])}
                    for edge in approved_relationships
                )
                fields_exist = (
                    left[1] in approved.get(str(left[0]), {})
                    and right[1] in approved.get(str(right[0]), {})
                )
                if fields_exist and (exact_relationship or not pair_has_declared_relationship):
                    valid_joins.append(join)
            joins = valid_joins
        filters = parsed.get("filters", [])
        if mode == "source_backed":
            valid_filters = []
            for item in filters:
                if not isinstance(item, dict):
                    continue
                obj, source_field = item.get("source_object"), item.get("source_field")
                field_meta = approved.get(str(obj), {}).get(str(source_field))
                if field_meta is None:
                    continue
                item["field"] = str(item.get("field") or f"{obj}_{source_field}")
                valid_filters.append(item)
            filters = valid_filters
        coverage = parsed.get("criteria_coverage", [])
        coverage_by_id = {str(item.get("criterion_id")): item for item in coverage if isinstance(item, dict)}
        normalized_coverage = []
        for criterion in intent.get("criteria", []):
            if not isinstance(criterion, dict):
                continue
            item = coverage_by_id.get(str(criterion.get("id")))
            if item is None:
                normalized_coverage.append({"criterion_id": criterion.get("id"), "covered": False, "source_object": None, "source_field": None, "explanation": "Gemma did not provide a coverage result for this requirement."})
                continue
            obj, source_field = item.get("source_object"), item.get("source_field")
            valid_ref = mode != "source_backed" or (source_field in approved.get(str(obj), {}))
            normalized_coverage.append({**item, "covered": bool(item.get("covered")) and valid_ref, "explanation": str(item.get("explanation") or "")[:240] if valid_ref else "The proposed source field is not in the approved schema."})
        notes = parsed.get("notes", [])
        if on_stage:
            on_stage("validating_suggestions_complete", duration_ms=int((perf_counter() - validation_started) * 1000))
        return fields, [item for item in joins if isinstance(item, dict)], [item for item in filters if isinstance(item, dict)], [item.strip() for item in notes if isinstance(item, str) and item.strip()][:20], model, intent, normalized_coverage

    def generate_query(self, requirement: str, structure: list[dict], fields: list[dict], relationships: list[dict], filters: list[dict], row_limit: int) -> tuple[str, str]:
        model = self._model()
        prompt = {
            "task": "Generate a read-only PostgreSQL SELECT for the user's reviewed test-data mapping.",
            "requirement": requirement,
            "approved_source_metadata": structure,
            "user_selected_output_fields": fields,
            "user_selected_relationships": relationships,
            "user_selected_filters": filters,
            "maximum_rows": row_limit,
            "rules": [
                "Return JSON with one key sql containing only one SELECT statement.",
                "Use only the selected source fields, selected relationships, and approved schema metadata.",
                "Alias each selected column to its user-selected target_field.",
                "Apply the user-selected filters and their values.",
                "Use filter values as SQL literals; do not add bind parameter placeholders.",
                "Include LIMIT equal to maximum_rows. Do not use SELECT * or modify data.",
                "Do not invent tables or columns.",
            ],
        }
        result = self._request("/api/chat", {
            "model": model, "stream": False, "format": "json",
            "options": {"temperature": 0.1, "num_predict": 1200},
            "messages": [{"role": "user", "content": json.dumps(prompt)}],
        })
        if result.get("done_reason") == "length":
            raise RuntimeError("Gemma stopped before completing the query. Retry with fewer selected fields or a shorter requirement.")
        try:
            parsed = json.loads(result.get("message", {}).get("content"))
            sql = parsed["sql"]
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError("Gemma did not return a valid SQL query") from exc
        if not isinstance(sql, str) or not sql.strip():
            raise RuntimeError("Gemma returned an empty SQL query")
        return sql.strip(), model

    def review_synthetic_sql(self, query: str, available_fields: list[dict]) -> tuple[str, str, str | None, str | None, list[dict]]:
        model = self._model()
        prompt = {
            "task": "Review common SQL syntax and repair syntax errors in the user's synthetic-data query.",
            "user_sql": query,
            "reviewed_output_fields": available_fields,
            "rules": [
                "Return JSON with keys syntax_valid, syntax_error, corrected_sql, fix_summary, and output_fields.",
                "Check SQL syntax only. Do not resolve, inspect, validate, invent, rename, or remove table and column names.",
                "Do not connect to, query, or use any source connection, source schema, source table, or external database.",
                "Treat table and column identifiers as logical names supplied by the user, not as a request to access those objects.",
                "Also identify the output columns projected by the final SELECT. Return output_fields as an ordered array of objects with name and data_type, using explicit aliases when present and otherwise the projected column name.",
                "For SELECT * or table.*, use the supplied reviewed_output_fields in their existing order. Never discover fields by looking up table names.",
                "Use a projected field's matching reviewed_output_fields data_type when available; infer an obvious SQL expression type when possible, otherwise use TEXT.",
                "If the query is invalid, determine output_fields from corrected_sql.",
                "If the SQL is syntactically valid, set syntax_valid to true and set syntax_error, corrected_sql, and fix_summary to null.",
                "If syntax is invalid, set syntax_valid to false, explain only the syntax issue in syntax_error, and return the minimally corrected full query in corrected_sql.",
                "fix_summary briefly describes the syntax change without repeating or quoting the SQL.",
                "Preserve the user's intent, CTEs, joins, aliases, window functions, filters, and identifiers. Change only syntax that is invalid.",
                "Do not generate sample rows in this review operation.",
                "Do not include markdown, comments, prose outside JSON, or multiple SQL statements.",
            ],
        }
        result = self._request("/api/chat", {
            "model": model, "stream": False, "format": "json",
            "options": {
                "temperature": 0.1,
                # A correction must return the full SQL. Allow longer outputs
                # for longer editor queries while keeping a firm cap.
                "num_predict": min(8192, max(2048, len(query) // 2)),
                "num_ctx": 16384,
            },
            "messages": [{"role": "user", "content": json.dumps(prompt)}],
        })
        if result.get("done_reason") == "length":
            raise RuntimeError("Gemma reached the SQL review output limit before returning the complete query. Shorten the SQL and retry.")
        try:
            parsed = json.loads(result.get("message", {}).get("content"))
            syntax_valid = parsed["syntax_valid"]
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError("Gemma did not return a valid SQL syntax review") from exc
        if not isinstance(syntax_valid, bool):
            raise RuntimeError("Gemma returned an invalid SQL syntax review")
        output_fields = parsed.get("output_fields")
        if not isinstance(output_fields, list) or not output_fields:
            raise RuntimeError("Gemma did not return the SQL output fields")
        normalized_fields = []
        seen_names = set()
        for field in output_fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            data_type = field.get("data_type")
            if not isinstance(name, str) or not name.strip() or not isinstance(data_type, str) or not data_type.strip():
                continue
            name = name.strip()
            if name.casefold() in seen_names:
                raise RuntimeError("Gemma returned duplicate SQL output fields")
            seen_names.add(name.casefold())
            normalized_fields.append({"name": name, "data_type": data_type.strip()})
        if not normalized_fields:
            raise RuntimeError("Gemma did not return valid SQL output fields")
        if syntax_valid:
            return query, model, None, None, normalized_fields
        repaired = parsed.get("corrected_sql")
        syntax_error = parsed.get("syntax_error")
        fix_summary = parsed.get("fix_summary")
        if not isinstance(repaired, str) or not repaired.strip():
            raise RuntimeError("Gemma identified a syntax error but returned no corrected SQL")
        if not isinstance(syntax_error, str) or not syntax_error.strip():
            syntax_error = "SQL syntax is invalid."
        if not isinstance(fix_summary, str) or not fix_summary.strip():
            fix_summary = "Corrected the SQL syntax."
        return repaired.strip(), model, syntax_error.strip(), fix_summary.strip(), normalized_fields

    def generate_synthetic_rows(self, requirement: str, fields: list[dict], relationships: list[dict], row_count: int, sql_query: str | None = None) -> tuple[list[dict], str]:
        model = self._model()
        output_fields = [
            {"name": item["target_field"], "type": item["data_type"], "required": item.get("required", False),
             "description": item.get("rationale") or "Use the field name and scenario to determine realistic content."}
            for item in fields
        ]
        rows: list[dict] = []
        batch_size = max(1, min(10, 120 // max(1, len(output_fields))))
        for start in range(0, row_count, batch_size):
            count = min(batch_size, row_count - start)
            properties = {item["target_field"]: {"type": _synthetic_json_type(item["data_type"])} for item in fields}
            prompt = {
                "task": "Generate realistic, fictional synthetic test data for a banking scenario.",
                "scenario_requirements": requirement,
                "user_sql_requirements": sql_query,
                "output_fields": output_fields,
                "key_relationships": relationships,
                "record_count": count,
                "response_shape": {"rows": [{item["target_field"]: "a value matching its field meaning and declared type" for item in fields}]},
                "rules": [
                    "Return one JSON object with a rows array containing exactly record_count records.",
                    "Return every output field in every record, using the exact output field names.",
                    "Generate plausible values that match each field's meaning, declared type, and the scenario requirements; do not use generic placeholders such as sample-1.",
                    "Apply the same contextual generation to every field added or renamed by the user: infer its meaning from the exact field name, declared type, rationale, scenario, and SQL requirements; when the meaning is ambiguous, make the most reasonable scenario-consistent choice instead of echoing the field name or using a placeholder.",
                    "For fields whose name or description indicates a phone, mobile, or telephone number, generate a realistic fictional phone-number string in a format appropriate to the scenario; do not use words, placeholders, or actual personal numbers.",
                    "Keep related values coherent across fields and records. Preserve the requested key relationships and realistic banking constraints when applicable.",
                    "When user_sql_requirements is provided, interpret its SELECT, JOIN, WHERE, grouping, ordering, CTE, and window-function logic as generation instructions. Create fictional output rows that satisfy the requested conditions and relationships; do not execute the SQL or access any source table or connection.",
                    "The reviewed output fields and their declared types are the final output schema. Include every reviewed field even if SQL uses qualified logical table names or aliases.",
                    "Treat identifiers in user_sql_requirements as descriptive instructions only. Never look up or retrieve records, metadata, credentials, or schemas associated with those identifiers.",
                    "Use fictional names, contact details, identifiers, and account details. Do not reproduce or claim to represent real people or real accounts.",
                    "Use JSON numbers for numeric fields, JSON booleans for boolean fields, ISO 8601 strings for date/time fields, UUID strings for UUID fields, arrays for array fields, and objects for JSON/object fields.",
                    "Do not include prose, markdown, or keys that are not in output_fields.",
                ],
            }
            result = self._request("/api/chat", {
                "model": model, "stream": False,
                "format": {
                    "type": "object", "properties": {
                        "rows": {
                            "type": "array", "items": {
                                "type": "object", "properties": properties,
                                "required": list(properties), "additionalProperties": False,
                            },
                            "minItems": count, "maxItems": count,
                        },
                    }, "required": ["rows"], "additionalProperties": False,
                },
                "options": {"temperature": 0.7, "num_predict": min(8192, max(1200, count * len(output_fields) * 48))},
                "messages": [{"role": "user", "content": json.dumps(prompt)}],
            })
            if result.get("done_reason") == "length":
                raise RuntimeError("Gemma stopped before completing the synthetic data. Retry with fewer fields or fewer records.")
            expected_names = [item["target_field"] for item in fields]
            batch = _decode_synthetic_rows(result.get("message", {}).get("content", ""), expected_names)
            if batch is None:
                raise RuntimeError("Gemma returned malformed synthetic data. Retry generation; if it repeats, reduce the selected fields or records.")
            if not isinstance(batch, list) or len(batch) != count or any(not isinstance(row, dict) for row in batch):
                raise RuntimeError("Gemma returned an incomplete synthetic data batch. Retry generation.")
            if any(not set(expected_names).issubset(row) for row in batch):
                raise RuntimeError("Gemma omitted one or more selected output fields. Retry generation.")
            rows.extend({name: row[name] for name in expected_names} for row in batch)
        return rows, model


def get_scenario_assistant() -> ScenarioAssistant:
    return OllamaScenarioAssistant()
