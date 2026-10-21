from email.message import Message
import http.client
import io
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from urllib.error import URLError,HTTPError

from social_read.images import download_image
from social_read.model import ReadError,request_diagnostics

URL='https://sns-webpic.xhscdn.com/fixture?signature=PRIVATE'

class Response(io.BytesIO):
    def __init__(self,data,length=None):
        super().__init__(data);self.headers=Message();self.headers['Content-Type']='image/png'
        if length is not None:self.headers['Content-Length']=str(length)
    def geturl(self):return URL

class ImageDownloadTests(unittest.TestCase):
    def fail_download(self,error):
        opener=Mock();opener.open.side_effect=error
        with tempfile.TemporaryDirectory() as td,patch('social_read.images.build_opener',return_value=opener),self.assertRaises(ReadError) as caught:
            download_image(URL,Path(td)/'image',threading.Event())
        opener.open.assert_called_once()
        result=caught.exception.result()
        self.assertNotIn('PRIVATE',str(result));self.assertNotIn('xhscdn',str(result))
        return result

    def test_transport_causes_are_distinct_without_retries_or_raw_errors(self):
        for reason,status,kind in [(socket.timeout('PRIVATE'),'image_timeout','timeout'),(socket.gaierror(-2,'PRIVATE'),'image_dns_failed','dns'),(ssl.SSLCertVerificationError(1,'PRIVATE'),'image_tls_failed','tls'),(ConnectionResetError('PRIVATE'),'image_connection_reset','connection_reset'),(ConnectionRefusedError('PRIVATE'),'image_connection_failed','connection_refused')]:
            with self.subTest(status=status):
                result=self.fail_download(URLError(reason));self.assertEqual(result['status'],status)
                self.assertEqual(result['diagnostics']['network_error'],kind)
                self.assertEqual(result['diagnostics']['failure_origin'],'image_connect')

    def test_http_denial_and_rate_limit_stop_without_retry(self):
        for code,status in [(401,'image_access_denied'),(403,'image_access_denied'),(429,'image_rate_limited')]:
            result=self.fail_download(HTTPError(URL,code,'PRIVATE',{},None))
            self.assertEqual(result['status'],status);self.assertEqual(result['diagnostics']['http_status'],code)
            self.assertEqual(result['diagnostics']['request_attempts'],1)

    def test_truncated_stream_is_not_sent_to_ocr_as_complete(self):
        opener=Mock();opener.open.return_value=Response(b'abc',10)
        with tempfile.TemporaryDirectory() as td,patch('social_read.images.build_opener',return_value=opener),self.assertRaises(ReadError) as caught:
            download_image(URL,Path(td)/'image',threading.Event())
        self.assertEqual(caught.exception.code,'image_incomplete')
        self.assertEqual(caught.exception.diagnostics['network_error'],'incomplete_body')

    def test_invalid_length_and_local_io_are_separate(self):
        for length,storage,status in [('invalid',False,'image_response_invalid'),(3,True,'image_storage_failed')]:
            opener=Mock();opener.open.return_value=Response(b'abc',length)
            with tempfile.TemporaryDirectory() as td,patch('social_read.images.build_opener',return_value=opener),self.assertRaises(ReadError) as caught:
                download_image(URL,Path(td) if storage else Path(td)/'image',threading.Event())
            self.assertEqual(caught.exception.code,status)

    def test_complete_image_download_preserves_bytes(self):
        opener=Mock();opener.open.return_value=Response(b'abc',3)
        with tempfile.TemporaryDirectory() as td,patch('social_read.images.build_opener',return_value=opener):
            path=Path(td)/'image';download_image(URL,path,threading.Event());self.assertEqual(path.read_bytes(),b'abc')

    def test_diagnostics_strip_unapproved_fields(self):
        result=request_diagnostics({'request_kind':'xiaohongshu_image','failure_origin':'image_read','network_error':'timeout','url':URL,'exception':'PRIVATE','cookie':'PRIVATE'})
        self.assertEqual(set(result),{'request_kind','failure_origin','network_error'})
