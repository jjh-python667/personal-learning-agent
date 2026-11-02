import asyncio
import io
import json
import sqlite3
import zipfile
import unittest
import test_core
import db
import agent

def word(xml):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml',xml)
    return output.getvalue()

class WordTests(unittest.TestCase):
    setUp = test_core.StudyTests.setUp
    tearDown = test_core.StudyTests.tearDown
    def test_word_order_and_references(self):
        xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:r><w:t>TCP三次握手</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格里的序号</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:r><w:t>最后的确认</w:t></w:r></w:p></w:body></w:document>'''
        result=self.client.post('/api/upload',files={'file':('网络.docx',word(xml),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
        self.assertEqual(result.status_code,200,result.text)
        doc=self.client.get('/api/documents/'+str(result.json()['id'])).json()
        self.assertEqual(doc['pages'],['TCP三次握手\n\n表格里的序号\n\n最后的确认'])
        self.assertEqual(doc['location_kind'],'section')
        self.assertEqual(db.sources('TCP')[0]['location_kind'],'section')
        read=asyncio.run(agent.tool('read_document',{'document_id':doc['id'],'page':1}))
        self.assertEqual(read['location_kind'],'section')
        source=db.sources('TCP')
        db.execute('INSERT INTO quizzes(question,expected,sources,created) VALUES(?,?,?,?)',('题目','答案',json.dumps(source),db.now()))
        self.assertEqual(self.client.get('/api/state').json()['quizzes'][0]['sources'][0]['location_kind'],'section')

    def test_word_rejects_invalid_empty_entity_and_old_doc(self):
        bad=[('old.doc',b'old'),('bad.docx',b'bad'),('entity.docx',word('<!DOCTYPE x [<!ENTITY a "x">]><x/>')),('empty.docx',word('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')),('large.docx',word('x'*(8*1024*1024+1)))]
        for filename,data in bad:
            with self.subTest(filename=filename):
                self.assertEqual(self.client.post('/api/upload',files={'file':(filename,data)}).status_code,400)

    def test_legacy_database_migration_preserves_documents(self):
        with db.connection() as con:
            con.execute('DROP TABLE documents')
            con.execute('CREATE TABLE documents(id INTEGER PRIMARY KEY,title TEXT,pages TEXT,created TEXT)')
            con.execute('INSERT INTO documents VALUES(1,?,?,?)',('旧资料','["原文"]',db.now()))
        db.init()
        db.init()
        self.assertEqual(db.rows('SELECT * FROM documents')[0]['location_kind'],'page')
        self.assertEqual(db.rows('SELECT * FROM documents')[0]['pages'],'["原文"]')
