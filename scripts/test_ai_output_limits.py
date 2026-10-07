"""Expanded output contracts; no database or external calls."""
import copy, json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_ai_output_validation as F
from app.schemas.ai_output_limits import REVIEW_LIMITS, SUMMARY_LIMITS
from app.services.ai import prompts, validators, token_budget
from app.services.ai.providers import registry
from app.services.ai.providers.json_provider import OpenAIProvider, GeminiProvider
from app.services.ai.providers.base import GenerationRequest
import httpx2

class ExpandedOutput(unittest.TestCase):
    def validate(self, kind, data, sent=F.SENT):
        return validators.validate_output(kind,data,sent,revision_id=F.REVISION_ID,schema_version=f'{kind}-output-v2')

    def test_review_boundaries_and_result_serialization(self):
        for field in ('title','condition','impact','verification','suggestion'):
            limit=REVIEW_LIMITS[field]
            data=F.review(F.finding(**{field:'가'*limit}),schema_version='review-output-v2')
            result=self.validate('review',data)
            self.assertTrue(result.ok, result.issues)
            self.assertEqual(len(result.findings[0].model_dump()[field]),limit)
            data['findings'][0][field]+='가'
            self.assertFalse(self.validate('review',data).ok)

    def test_long_quote_must_still_be_real_and_in_range(self):
        sent=copy.deepcopy(F.SENT); sent.files[0].right_text[10]='가'*2000
        data=F.review(F.finding(location={'path':'src/app.py','side':'RIGHT','start_line':10,'end_line':10,'quote':'가'*2000}),schema_version='review-output-v2')
        self.assertTrue(self.validate('review',data,sent).ok)
        data['findings'][0]['location']['quote']='나'*400
        self.assertIn('quote_not_in_range',[i.code for i in self.validate('review',data,sent).issues])
        data['findings'][0]['location']['quote']='가'*2001
        self.assertIn('schema_string_too_long',[i.code for i in self.validate('review',data,sent).issues])

    def test_limitations_and_summary_boundaries(self):
        for length,ok in ((1000,True),(1001,False)):
            self.assertEqual(self.validate('review',F.review(F.finding(limitations=['가'*length]),schema_version='review-output-v2')).ok,ok)
        for field,limit in SUMMARY_LIMITS.items():
            for length,ok in ((limit,True),(limit+1,False)):
                data=F.summary(schema_version='summary-output-v2')
                if field=='file_changes[].summary': data['file_changes'][0]['summary']='가'*length
                elif field=='unknowns[]': data['unknowns']=['가'*length]
                else: data[field]='가'*length
                self.assertEqual(self.validate('summary',data).ok,ok,(field,length))

    def test_old_contract_and_wrong_version_remain_rejected(self):
        data=F.review(F.finding(title='가'*300))
        self.assertFalse(validators.validate_output('review',data,F.SENT,revision_id=F.REVISION_ID).ok)
        self.assertFalse(self.validate('review',data).ok)

    def test_prompt_and_server_share_limits(self):
        prompts.verify_prompt_digests()
        for kind,limits in (('review',REVIEW_LIMITS),('summary',SUMMARY_LIMITS)):
            text=prompts.render_request(kind,F.SENT,prompt_version=f'{kind}-v2').system
            self.assertIn(f'{kind}-output-v2',text)
            for field,limit in limits.items(): self.assertIn(f'{field}: at most {limit} characters',text)
        self.assertIn('review-output-v1',prompts.system_template('review'))

    def test_provider_schema_lengths_and_descriptions(self):
        provider=OpenAIProvider(registry.get_profile('gpt-5.4-v3'),api_key='fake-key')
        schema=provider.params(GenerationRequest('review','system','data'))['text']['format']['schema']
        finding=schema['properties']['findings']['items']['properties']
        self.assertEqual(finding['location']['properties']['quote']['maxLength'],2000)
        self.assertEqual(finding['condition']['maxLength'],6000)
        other=GeminiProvider(registry.get_profile('gemini-3.5-flash-lite-v3'),api_key='fake-key')
        self.assertIn('6000',other.output_schema('review')['properties']['findings']['items']['properties']['condition']['description'])

    def test_profile_versioning_preserves_cost_cap(self):
        for prefix in ('claude-opus-5-5','claude-sonnet-5-5','gpt-5.4','gemini-3.5-flash-lite'):
            old,new=registry.get_profile(prefix+'-v2'),registry.get_profile(prefix+'-v3')
            self.assertNotEqual(old.fingerprint(),new.fingerprint())
            self.assertEqual(new.review_schema,'review-output-v2')
            self.assertEqual(token_budget.max_cost(old,64000),token_budget.max_cost(new,64000))
            self.assertEqual(new.max_output_tokens,4000)

    def test_count_and_generate_transmit_identical_expanded_schema(self):
        requests=[]
        def respond(request):
            requests.append(json.loads(request.content))
            if request.url.path.endswith('/input_tokens'):
                return httpx2.Response(200,json={'input_tokens':100})
            return httpx2.Response(200,json={'id':'mock','status':'completed','output':[{'type':'message','content':[
                {'type':'output_text','text':'{"schema_version":"review-output-v2","findings":[]}'}]}],
                'usage':{'input_tokens':100,'output_tokens':20}})
        with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
            provider=OpenAIProvider(registry.get_profile('gpt-5.4-v3'),api_key='fake-key',client=client)
            request=GenerationRequest('review','system','data')
            provider.count_tokens(request); provider.generate(request)
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0]['text']['format'],requests[1]['text']['format'])
        self.assertEqual(requests[1]['max_output_tokens'],4000)

    def test_model_list_shows_only_latest_cloud_profiles_with_original_names(self):
        expected={'claude-opus-5-5-v3':'Claude Opus 5.5','claude-sonnet-5-5-v3':'Claude Sonnet 5.5',
                  'gpt-5.4-v3':'GPT-5.4','gemini-3.5-flash-lite-v3':'Gemini 3.5 Flash-Lite'}
        actual={m['profile_id']:m['display_name'] for p in registry.supported_options()
                for m in p['models'] if m['execution_location']=='external'}
        self.assertEqual(actual,expected)
        self.assertEqual(registry.get_profile('gpt-5.4-v2').review_schema,'review-output-v1')

if __name__=='__main__': unittest.main(verbosity=2)
