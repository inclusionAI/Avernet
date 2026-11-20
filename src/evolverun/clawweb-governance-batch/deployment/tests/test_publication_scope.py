import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT.parent),str(ROOT.parent/'tests')]
from readiness import JournalCenter
from clawweb_batch.core import API_PREFIX
from clawweb_batch.pipeline import run
from clawweb_batch.config import Config
from test_batch import NOW

class PublicationScopeTests(TestCase):
    def test_creation_only_transport_refuses_every_other_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            underlying=Mock();underlying.write.return_value={'improvementId':123,'status':'PENDING_ADMIN','adminReviewStatus':'PENDING'}
            center=JournalCenter(underlying,tmp,create_only=True)
            for path,payload in [(API_PREFIX+'/verification-results',{}),(API_PREFIX+'/verification-results/open',{}),
                                  (API_PREFIX+'/actions',{'actionType':'DIRECT_EVOLUTION'})]:
                with self.assertRaises(PermissionError):center.write(path,payload,'key')
            underlying.write.assert_not_called()
            receipt=center.write(API_PREFIX+'/actions',{'actionType':'ASSIGN_OWNER'},'create')
            self.assertEqual(receipt['status'],'PENDING_ADMIN')
            underlying.write.assert_called_once()

    def test_governance_only_never_reads_or_writes_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            cfg=Config(project='example',clawweb_url='https://example.test',output_dir=p/'out',state_dir=p/'state',
                       nas_roots=(p,),llm_config_file=p/'llm.json',cron_only=True,allow_writes=True)
            source=Mock();source.daily_counts.return_value=[{'dt':'20260928','is_cron':1,'raw_sampled_cnt':3}];source.ranking.return_value=[]
            center=Mock();nas=Mock();nas.available.return_value=[]
            report=run(cfg,source,center,Mock(),nas,now=NOW,apply=True,include_verification=False)
            self.assertEqual(report['status'],'SUCCEEDED')
            self.assertFalse(report['verification_enabled'])
            self.assertEqual(report['verification'],[])
            center.verification_candidates.assert_not_called();center.write.assert_not_called()
