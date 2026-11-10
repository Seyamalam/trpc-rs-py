"""Built-in playground panel: a self-contained UI served under the tRPC prefix.

MISSION section 2.7: `GET {path}/panel` returns a single HTML page — no CDN,
no external assets — that lists the router's procedures with their input and
output JSON schemas, generates input forms from those schemas, calls
procedures over the same tRPC wire protocol the TypeScript client uses, and
shows subscription streams live via `EventSource`. `GET {path}/panel/schema`
returns the introspection document the page renders.

The panel is *meta*: requests to `{path}/panel*` are answered before
procedure dispatch, so context factories and middleware never run for them,
and a procedure literally named `panel` is shadowed while the panel is
enabled. Enablement lives in `mount`/`attach`: on by default in development,
off in production unless `panel=True` is passed explicitly.

The introspection document has the shape::

    {"procedures": [
        {"path": "posts.by_id", "kind": "query",
         "input": <JSON schema or null>, "output": <JSON schema or null>,
         "rest": {"method": "GET", "path": "/posts/{post_id}"}},
        ...]}

`input` is an object schema whose properties are the handler's parameters —
exactly the JSON object the wire input expects — and `output` is the return
annotation's schema (a subscription's yielded item schema). Any annotation
Pydantic cannot turn into a JSON schema yields `null` for that side instead
of breaking the endpoint.
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING, Any, cast

from pydantic import ConfigDict, TypeAdapter, create_model

from .router import (
    Procedure,
    ProcedureKind,
    Router,
    subscription_item_annotation,
    type_hints_of,
)

if TYPE_CHECKING:
    from .asgi import Scope, Send

_PANEL_NAME = "panel"


def introspection_document(router: Router) -> dict[str, Any]:
    """Build the introspection document served at `{path}/panel/schema`.

    Args:
        router: The router to describe; a snapshot of the procedures
            registered at call time.

    Returns:
        A JSON-ready ``{"procedures": [...]}`` mapping — see the module
        docstring for the entry shape.

    Example:
        ```python
        from trpc_rs_py import Router
        from trpc_rs_py.panel import introspection_document

        router = Router()

        @router.query
        def health() -> bool:
            return True

        doc = introspection_document(router)
        assert doc["procedures"][0]["path"] == "health"
        ```
    """
    return {"procedures": [_describe(procedure) for procedure in router.procedures.values()]}


def introspection_json(router: Router) -> str:
    """Return `introspection_document(router)` serialized to JSON text.

    Built once at mount/attach time and replayed per request, so procedures
    registered after the adapter is constructed do not appear.

    Args:
        router: The router to describe.

    Returns:
        The serialized introspection document.

    Example:
        ```python
        import json

        from trpc_rs_py import Router
        from trpc_rs_py.panel import introspection_json

        assert json.loads(introspection_json(Router())) == {"procedures": []}
        ```
    """
    return json.dumps(introspection_document(router))


def panel_subpath(procedure_path: str) -> str | None:
    """Map a procedure path to its panel subpath, or `None` when not a panel path.

    `"panel"` maps to `""` (the page itself) and `"panel/<x>"` to `"<x>"`
    (a sub-resource such as `schema`). Any other value — including
    `"panel.foo"`, which remains a valid procedure path — returns `None`.

    Args:
        procedure_path: The path after the mount prefix, e.g. `panel/schema`.

    Returns:
        The panel subpath, or `None` for non-panel paths.

    Example:
        ```python
        from trpc_rs_py.panel import panel_subpath

        assert panel_subpath("panel") == ""
        assert panel_subpath("panel/schema") == "schema"
        assert panel_subpath("panel.foo") is None
        ```
    """
    if procedure_path == _PANEL_NAME:
        return ""
    if procedure_path.startswith(f"{_PANEL_NAME}/"):
        return procedure_path[len(_PANEL_NAME) + 1 :]
    return None


async def serve_panel(scope: Scope, send: Send, *, subpath: str, document: str) -> None:
    """Answer one `{path}/panel*` request.

    `GET` serves the page for an empty `subpath` and the introspection
    document for `schema`; unknown subpaths 404 and non-GET methods 405 with
    `Allow: GET`. Responses are plain HTML/JSON, not tRPC envelopes — the
    panel sits beside the protocol, not inside it.

    Args:
        scope: Raw ASGI scope for an `http` request.
        send: ASGI send callable.
        subpath: The panel subpath from `panel_subpath`.
        document: Serialized introspection document for the `schema` route.

    Example:
        ```python
        # Inside dispatch, once `panel_subpath` matched:
        # await serve_panel(scope, send, subpath="schema", document=doc)
        ```
    """
    method: str = scope["method"]
    if method != "GET":
        await _respond(
            send,
            405,
            json.dumps(_error("METHOD_NOT_ALLOWED", "Method not allowed")),
            "application/json",
            ((b"allow", b"GET"),),
        )
        return
    if subpath == "":
        await _respond(send, 200, _PANEL_PAGE, "text/html; charset=utf-8")
        return
    if subpath == "schema":
        await _respond(send, 200, document, "application/json")
        return
    await _respond(send, 404, json.dumps(_error("NOT_FOUND", "Not found")), "application/json")


def _describe(procedure: Procedure) -> dict[str, Any]:
    """Build one `procedures` entry for the introspection document."""
    entry: dict[str, Any] = {
        "path": procedure.path,
        "kind": procedure.kind.value,
        "input": _input_schema(procedure),
        "output": _output_schema(procedure),
    }
    if procedure.rest is not None:
        entry["rest"] = {"method": procedure.rest.method, "path": procedure.rest.path}
    return entry


def _input_schema(procedure: Procedure) -> dict[str, Any] | None:
    """Return the JSON schema of the wire input object, or `None`.

    Mirrors `build_input_validator`: every ordinary (non-`ctx`, non-vararg)
    parameter becomes a property — annotated ones with their annotation's
    schema, unannotated ones as unconstrained `{}` — and `**kwargs` opens
    the object (`additionalProperties`). `*args`-only handlers take a JSON
    list/scalar instead, described as an unconstrained array schema, and
    parameterless handlers get `None` (no input). Any schema-generation
    failure returns `None` rather than breaking the document.
    """
    try:
        signature = inspect.signature(procedure.handler)
    except (TypeError, ValueError):
        return None
    parameters = list(signature.parameters.values())
    if parameters and parameters[0].name == "ctx":
        # Injected request context, never client input (trpc_rs_py.context).
        parameters = parameters[1:]
    ordinary = [
        parameter
        for parameter in parameters
        if parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    if not ordinary:
        if any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
            return {"type": "array", "items": {}}
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return {"type": "object"}
        return None
    hints = type_hints_of(procedure.handler)
    accepts_extra = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters)
    fields: dict[str, tuple[Any, Any]] = {}
    for parameter in ordinary:
        annotation = hints.get(parameter.name, parameter.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = Any
        default = parameter.default if parameter.default is not inspect.Parameter.empty else ...
        fields[parameter.name] = (annotation, default)
    try:
        model = create_model(
            f"{getattr(procedure.handler, '__name__', 'Procedure')}Input",
            __config__=ConfigDict(
                extra="allow" if accepts_extra else "forbid",
                populate_by_name=True,
            ),
            **cast(Any, fields),
        )
        schema = TypeAdapter(model).json_schema()
    except Exception:
        return None
    schema.pop("title", None)
    return schema


def _output_schema(procedure: Procedure) -> dict[str, Any] | None:
    """Return the JSON schema of the result payload, or `None`.

    For subscriptions the schema describes one yielded item (`Tracked`
    unwrapped), matching what `EventSource` delivers per `data:` frame. Any
    schema-generation failure — an unannotated return, a non-serializable
    annotation — returns `None`.
    """
    annotation = procedure.output_annotation
    if annotation is None:
        return None
    if procedure.kind is ProcedureKind.SUBSCRIPTION:
        annotation = subscription_item_annotation(annotation)
        if annotation is None:
            return None
    try:
        return TypeAdapter(annotation).json_schema()
    except Exception:
        return None


def _error(code: str, message: str) -> dict[str, Any]:
    """Build the fixed error body ``{"error": {"code", "message"}}``."""
    return {"error": {"code": code, "message": message}}


async def _respond(
    send: Send,
    status: int,
    body: str,
    content_type: str,
    extra_headers: tuple[tuple[bytes, bytes], ...] = (),
) -> None:
    """Send a complete HTTP response with `body` and `content_type`."""
    payload = body.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type.encode("ascii")),
                (b"content-length", str(len(payload)).encode("ascii")),
                *extra_headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


_PANEL_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>tRPC panel</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;height:100vh;display:flex;font-family:ui-sans-serif,system-ui,sans-serif;background:light-dark(#fff,#101216);color:light-dark(#1b1e24,#e6e8ec)}
#side{width:19rem;min-width:13rem;border-right:1px solid light-dark(#e3e5ea,#262b34);display:flex;flex-direction:column}
#search{margin:.7rem;padding:.45rem .6rem;font:inherit;border:1px solid light-dark(#d2d5db,#333a46);border-radius:6px;background:transparent;color:inherit}
#procs{flex:1;overflow-y:auto;padding:0 .45rem .8rem}
.g{color:light-dark(#6b7280,#8b93a1);font-size:.68rem;text-transform:uppercase;letter-spacing:.07em;margin:.85rem .35rem .25rem}
.proc{display:flex;gap:.55rem;align-items:baseline;padding:.33rem .45rem;border-radius:6px;cursor:pointer;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.84rem}
.proc:hover{background:light-dark(#f0f2f5,#1a1f27)}
.proc.sel{background:light-dark(#e6edfd,#20304f)}
.k{font-size:.6rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;min-width:4.6rem}
.k.query{color:#0a7d2c}.k.mutation{color:#0b5bd3}.k.subscription{color:#7a4bbf}
#main{flex:1;overflow-y:auto;padding:1.4rem 2rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86rem}
h1{font-size:1.1rem;margin:0 0 1rem;font-weight:600;word-break:break-all}
h2{font-size:.92rem;margin:1.3rem 0 .4rem}
.muted{color:light-dark(#6b7280,#8b93a1)}
label{display:block;margin:.6rem 0 .2rem;font-size:.8rem}
label>span{display:block;margin-bottom:.2rem}
.req{color:#b3261e}
input[type=text],input[type=number],textarea,select{width:100%;font:inherit;padding:.4rem .55rem;border:1px solid light-dark(#d2d5db,#333a46);border-radius:6px;background:light-dark(#fff,#161a21);color:inherit}
textarea{min-height:4rem;resize:vertical}
button{font:inherit;padding:.45rem 1rem;border:1px solid light-dark(#d2d5db,#333a46);border-radius:6px;background:light-dark(#f5f6f8,#1d2330);color:inherit;cursor:pointer;margin-top:.8rem}
button:hover{border-color:#0b5bd3}
button:disabled{opacity:.5;cursor:default}
pre{background:light-dark(#f5f6f8,#141820);border:1px solid light-dark(#e3e5ea,#262b34);border-radius:6px;padding:.7rem;overflow:auto;max-height:24rem}
details{margin:.4rem 0}
summary{cursor:pointer;color:light-dark(#6b7280,#8b93a1);font-size:.78rem}
.err{color:#b3261e;font-size:.8rem;margin-top:.5rem;white-space:pre-wrap}
.tag{display:inline-block;font-size:.62rem;padding:.12rem .45rem;border-radius:99px;border:1px solid currentColor;margin-left:.55rem;vertical-align:middle}
.ev{border-bottom:1px dashed light-dark(#e3e5ea,#262b34);padding:.4rem 0;white-space:pre-wrap;font-size:.8rem}
.ev .lab{color:light-dark(#6b7280,#8b93a1);font-weight:700}
</style></head><body>
<aside id="side"><input id="search" type="text" placeholder="filter procedures…"><div id="procs"></div></aside>
<main id="main"><p class="muted">Select a procedure on the left.</p></main>
<script>
(async()=>{
const base=location.pathname.replace(/panel\/?$/,"");
const procsEl=document.getElementById("procs"),main=document.getElementById("main"),search=document.getElementById("search");
let doc;
try{doc=await (await fetch(base+"panel/schema")).json();}
catch(e){main.innerHTML="";const p=document.createElement("pre");p.className="err";p.textContent="Failed to load "+base+"panel/schema: "+e;main.appendChild(p);return;}
const procs=(doc.procedures||[]).slice().sort((a,b)=>a.path.localeCompare(b.path));
const items=[],SKIP=Symbol("skip");let current=null;
const groups=new Map();
for(const p of procs){const i=p.path.lastIndexOf("."),g=i<0?"":p.path.slice(0,i);if(!groups.has(g))groups.set(g,[]);groups.get(g).push(p);}
for(const[g,ps]of groups){
 if(g){const h=document.createElement("div");h.className="g";h.textContent=g;procsEl.appendChild(h);}
 for(const p of ps){
  const el=document.createElement("div");el.className="proc";el.dataset.path=p.path;
  const k=document.createElement("span");k.className="k "+p.kind;k.textContent=p.kind;
  const n=document.createElement("span");n.textContent=p.path.slice(g?g.length+1:0);
  el.appendChild(k);el.appendChild(n);el.onclick=()=>select(p,el);
  procsEl.appendChild(el);items.push(el);}}
search.addEventListener("input",()=>{
 const q=search.value.toLowerCase();
 for(const el of items)el.style.display=el.dataset.path.toLowerCase().includes(q)?"":"none";
 for(const h of procsEl.querySelectorAll(".g")){
  let n=h.nextElementSibling,any=false;
  while(n&&n.classList.contains("proc")){if(n.style.display!=="none")any=true;n=n.nextElementSibling;}
  h.style.display=any?"":"none";}});
function select(p,el){
 for(const e of items)e.classList.toggle("sel",e===el);
 if(current&&current.src)current.src.close();
 main.innerHTML="";
 const h=document.createElement("h1");h.textContent=p.path;
 const tag=document.createElement("span");tag.className="tag k "+p.kind;tag.textContent=p.kind;h.appendChild(tag);
 if(p.rest){const r=document.createElement("span");r.className="tag";r.textContent=p.rest.method+" "+p.rest.path;h.appendChild(r);}
 main.appendChild(h);
 for(const[lab,schema]of[["input schema",p.input],["output schema",p.output]]){
  if(schema==null)continue;
  const d=document.createElement("details"),s=document.createElement("summary"),pre=document.createElement("pre");
  s.textContent=lab;pre.textContent=JSON.stringify(schema,null,2);d.appendChild(s);d.appendChild(pre);main.appendChild(d);}
 const form=document.createElement("div");main.appendChild(form);
 const collect=buildFields(p.input,form);
 const err=document.createElement("div");err.className="err";main.appendChild(err);
 if(p.kind==="subscription"){
  const pane=streamPane();
  main.appendChild(action("Subscribe",()=>{
   err.textContent="";let input;try{input=collect();}catch(e){err.textContent="Input JSON: "+e.message;return;}
   pane.open(p,input);}));
  main.appendChild(pane.el);current=pane;
 }else{
  const out=document.createElement("pre");out.style.display="none";
  main.appendChild(action("Call "+p.kind,async btn=>{
   err.textContent="";let input;try{input=collect();}catch(e){err.textContent="Input JSON: "+e.message;return;}
   btn.disabled=true;try{await runCall(p,input,out);}finally{btn.disabled=false;}}));
  main.appendChild(out);}}
function action(label,fn){const b=document.createElement("button");b.textContent=label;b.onclick=()=>fn(b);return b;}
async function runCall(p,input,out){
 let r;
 if(p.kind==="mutation"){
  r=await fetch(base+p.path,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(input===undefined?null:input)});
 }else{
  const q=input===undefined?"":"?input="+encodeURIComponent(JSON.stringify(input));
  r=await fetch(base+p.path+q);}
 const t=await r.text();let pretty=t;
 try{pretty=JSON.stringify(JSON.parse(t),null,2);}catch(_e){}
 out.style.display="block";out.textContent=r.status+" "+r.statusText+"\n"+pretty;}
function deref(s,root){for(let i=0;s&&s.$ref&&i<8;i++)s=root.$defs&&root.$defs[s.$ref.split("/").pop()];return s&&!s.$ref?s:{};}
function fieldFor(name,raw,required,root){
 const sub=deref(raw,root)||{};
 const label=document.createElement("label"),title=document.createElement("span");
 title.textContent=name;if(required){const star=document.createElement("span");star.className="req";star.textContent=" *";title.appendChild(star);}
 label.appendChild(title);
 const enums=sub.enum||(sub.const!==undefined?[sub.const]:null)||
  (Array.isArray(sub.anyOf)&&sub.anyOf.length&&sub.anyOf.every(o=>o.const!==undefined)?sub.anyOf.map(o=>o.const):null);
 let get;
 if(enums){
  const el=document.createElement("select");
  if(!required)el.appendChild(new Option("—",""));
  for(const v of enums)el.appendChild(new Option(JSON.stringify(v),JSON.stringify(v)));
  label.appendChild(el);get=()=>el.value===""?SKIP:JSON.parse(el.value);
 }else if(sub.type==="boolean"){
  const el=document.createElement("input");el.type="checkbox";label.appendChild(el);
  get=()=>el.checked||required?el.checked:SKIP;
 }else if(sub.type==="integer"||sub.type==="number"){
  const el=document.createElement("input");el.type="number";if(sub.type==="integer")el.step="1";
  if(sub.default!==undefined)el.placeholder=String(sub.default);label.appendChild(el);
  get=()=>el.value===""?SKIP:Number(el.value);
 }else if(sub.type==="string"){
  const el=document.createElement("input");el.type="text";
  if(sub.default!==undefined)el.placeholder=String(sub.default);label.appendChild(el);
  get=()=>el.value===""&&!required?SKIP:el.value;
 }else if(Object.keys(sub).length===0){
  const el=document.createElement("input");el.type="text";el.placeholder="any JSON value";label.appendChild(el);
  get=()=>{const t=el.value;if(t==="")return SKIP;try{return JSON.parse(t);}catch(_e){return t;}};
 }else{
  const el=document.createElement("textarea");el.rows=2;el.placeholder="JSON";label.appendChild(el);
  get=()=>{const t=el.value.trim();if(t==="")return SKIP;return JSON.parse(t);};}
 return{label,get:()=>[name,get()]};}
function buildFields(schema,container){
 if(schema==null){const m=document.createElement("p");m.className="muted";m.textContent="No input.";container.appendChild(m);return()=>undefined;}
 if(schema.type==="object"&&schema.properties){
  const req=new Set(schema.required||[]);
  const fields=Object.entries(schema.properties).map(([n,r])=>fieldFor(n,r,req.has(n),schema));
  for(const f of fields)container.appendChild(f.label);
  return()=>{const out={};for(const f of fields){const[n,v]=f.get();if(v!==SKIP)out[n]=v;}return Object.keys(out).length?out:undefined;};}
 const label=document.createElement("label"),t=document.createElement("span"),ta=document.createElement("textarea");
 t.textContent="input (JSON)";ta.rows=4;label.appendChild(t);label.appendChild(ta);container.appendChild(label);
 return()=>{const v=ta.value.trim();return v===""?undefined:JSON.parse(v);};}
function streamPane(){
 const el=document.createElement("div");
 const h=document.createElement("h2");h.textContent="stream";el.appendChild(h);
 const rl=document.createElement("label"),rs=document.createElement("span"),ri=document.createElement("input");
 rs.textContent="lastEventId (resume)";ri.type="text";rl.appendChild(rs);rl.appendChild(ri);el.appendChild(rl);
 const log=document.createElement("div");el.appendChild(log);
 const pane={el,src:null,
  open(p,input){
   if(pane.src)pane.src.close();log.innerHTML="";
   const qs=[];
   if(input!==undefined)qs.push("input="+encodeURIComponent(JSON.stringify(input)));
   if(ri.value.trim())qs.push("lastEventId="+encodeURIComponent(ri.value.trim()));
   const src=new EventSource(base+p.path+(qs.length?"?"+qs.join("&"):""));pane.src=src;
   const add=(lab,data,id)=>{
    const d=document.createElement("div");d.className="ev";let pretty=data;
    try{pretty=JSON.stringify(JSON.parse(data),null,2);}catch(_e){}
    const l=document.createElement("span");l.className="lab";l.textContent=lab+(id?" #"+id:"");
    d.appendChild(l);d.appendChild(document.createTextNode("\n"+pretty));log.appendChild(d);
    if(id)ri.value=id;};
   src.onmessage=e=>add("data",e.data,e.lastEventId);
   src.addEventListener("connected",()=>add("event","connected"));
   src.addEventListener("return",()=>{add("event","return — stream ended");src.close();});
   src.addEventListener("serialized-error",e=>add("error",e.data));
   src.addEventListener("ping",()=>add("event","ping"));
   src.onerror=()=>add("event","connection lost — EventSource retries automatically");}};
 el.appendChild(action("Close",()=>{if(pane.src)pane.src.close();}));
 return pane;}
})();
</script></body></html>
"""  # noqa: E501
