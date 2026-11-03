"""Lazy adapters: the web service starts without downloading models."""
import os
import threading
from pathlib import Path
import numpy as np
from PIL import Image

lock=threading.RLock()
_processor=None
_language_model=None
_tokenizer=None

def segment(rgb,prompt):
    global _processor
    import torch
    if not torch.cuda.is_available():raise ValueError('SAM 3 requires a configured CUDA GPU runtime. No prediction was made.')
    with lock:
        if _processor is None:
            from sam3.model_builder import build_sam3_image_model
            from sam3.model.sam3_image_processor import Sam3Processor
            from huggingface_hub import hf_hub_download
            checkpoint=os.getenv('SAM3_CHECKPOINT')
            if not checkpoint:
                token=os.getenv('HF_TOKEN')
                if not token:raise ValueError('Set HF_TOKEN on the backend or SAM3_CHECKPOINT. Never put it in the dashboard.')
                checkpoint=hf_hub_download('facebook/sam3','sam3.pt',token=token)
            repo=Path(os.getenv('SAM3_REPO','/content/sam3'))
            assets=list(repo.rglob('bpe_simple_vocab_16e6.txt.gz')) if repo.exists() else []
            kwargs=dict(checkpoint_path=checkpoint,load_from_HF=False,device='cuda',compile=False)
            if assets:kwargs['bpe_path']=str(assets[0])
            model=build_sam3_image_model(**kwargs).eval()
            _processor=Sam3Processor(model,device='cuda',confidence_threshold=.5)
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
            state=_processor.set_image(Image.fromarray(rgb))
            result=_processor.set_text_prompt(state=state,prompt=prompt)
            masks=result['masks'].detach().cpu().numpy().reshape(-1,*rgb.shape[:2]).astype(bool)
            scores=result['scores'].detach().float().cpu().numpy().reshape(-1)
        return masks,scores

def text_completion(messages,tools=None):
    """OpenAI-compatible local/open-source model endpoint; no arbitrary code execution."""
    if os.getenv('GEODEVELOP_LLM_PROVIDER')=='transformers':
        return local_completion(messages,tools)
    import httpx
    endpoint=os.getenv('GEODEVELOP_LLM_URL','http://127.0.0.1:11434/v1')
    model=os.getenv('GEODEVELOP_LLM_MODEL','qwen2.5:7b')
    key=os.getenv('GEODEVELOP_LLM_KEY','')
    payload=dict(model=model,messages=messages,temperature=.1,max_tokens=1400)
    if tools:payload.update(tools=tools,tool_choice='auto')
    try:
        response=httpx.post(endpoint.rstrip('/')+'/chat/completions',json=payload,
            headers={'Authorization':'Bearer '+key} if key else {},timeout=120)
        response.raise_for_status()
        return response.json()['choices'][0]['message']
    except (httpx.HTTPError,KeyError,IndexError) as e:
        raise ValueError('Language model unavailable. Configure GEODEVELOP_LLM_URL and GEODEVELOP_LLM_MODEL on the backend, or run Ollama with qwen2.5:7b. Manual analysis remains available.') from None

def local_completion(messages,tools):
    """Optional free local Qwen adapter, suitable for a Colab prototype; accuracy needs evaluation."""
    global _language_model,_tokenizer
    import torch,json,re,uuid
    from transformers import AutoTokenizer,AutoModelForCausalLM
    with lock:
        if _language_model is None:
            name=os.getenv('GEODEVELOP_TRANSFORMERS_MODEL','Qwen/Qwen2.5-1.5B-Instruct')
            _tokenizer=AutoTokenizer.from_pretrained(name)
            _language_model=AutoModelForCausalLM.from_pretrained(name,torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,device_map='auto').eval()
        # Qwen's documented tool-chat template emits structured <tool_call> blocks.
        chat=_tokenizer.apply_chat_template(messages,tools=tools,tokenize=False,add_generation_prompt=True)
        inputs=_tokenizer(chat,return_tensors='pt').to(_language_model.device)
        with torch.inference_mode():
            ids=_language_model.generate(**inputs,max_new_tokens=1200,do_sample=False,pad_token_id=_tokenizer.eos_token_id)
        text=_tokenizer.decode(ids[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True)
        calls=[]
        for raw in re.findall(r'<tool_call>\s*(.*?)\s*</tool_call>',text,re.S):
            value=json.loads(raw);calls.append(dict(id=uuid.uuid4().hex,type='function',function=dict(name=value['name'],arguments=json.dumps(value.get('arguments',{})))))
        return dict(role='assistant',content=re.sub(r'<tool_call>.*?</tool_call>','',text,flags=re.S).strip(),**({'tool_calls':calls} if calls else {}))
