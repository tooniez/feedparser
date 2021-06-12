# The public API for feedparser
# Copyright 2010-2020 Kurt McKee <contactme@kurtmckee.org>
# Copyright 2002-2008 Mark Pilgrim
# All rights reserved.
#
# This file is a part of feedparser.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice,
#   this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS 'AS IS'
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import datetime
import io
import time
from typing import Dict, List, Union
import urllib.parse
import xml.sax

from .datetimes import registerDateHandler, _parse_date
from .encodings import convert_to_utf8
from .html import BaseHTMLProcessor
from . import http
from .mixin import XMLParserMixin
from .parsers.loose import LooseXMLParser
from .parsers.strict import StrictXMLParser
from .parsers.json import JSONParser
from .sanitizer import replace_doctype
from .urls import convert_to_idn, make_safe_absolute_uri
from .util import FeedParserDict


# List of preferred XML parsers, by SAX driver name.  These will be tried first,
# but if they're not installed, Python will keep searching through its own list
# of pre-installed parsers until it finds one that supports everything we need.
PREFERRED_XML_PARSERS = ["drv_libxml2"]

_XML_AVAILABLE = True

SUPPORTED_VERSIONS = {
    '': 'unknown',
    'rss090': 'RSS 0.90',
    'rss091n': 'RSS 0.91 (Netscape)',
    'rss091u': 'RSS 0.91 (Userland)',
    'rss092': 'RSS 0.92',
    'rss093': 'RSS 0.93',
    'rss094': 'RSS 0.94',
    'rss20': 'RSS 2.0',
    'rss10': 'RSS 1.0',
    'rss': 'RSS (unknown version)',
    'atom01': 'Atom 0.1',
    'atom02': 'Atom 0.2',
    'atom03': 'Atom 0.3',
    'atom10': 'Atom 1.0',
    'atom': 'Atom (unknown version)',
    'cdf': 'CDF',
    'json1': 'JSON feed 1',
}


def _open_resource(url_file_stream_or_string, etag, modified, agent, referrer, handlers, request_headers, result):
    """URL, filename, or string --> stream

    This function lets you define parsers that take any input source
    (URL, pathname to local or network file, or actual data as a string)
    and deal with it in a uniform manner.  Returned object is guaranteed
    to have all the basic stdio read methods (read, readline, readlines).
    Just .close() the object when you're done with it.

    If the etag argument is supplied, it will be used as the value of an
    If-None-Match request header.

    If the modified argument is supplied, it can be a tuple of 9 integers
    (as returned by gmtime() in the standard Python time module) or a date
    string in any format supported by feedparser. Regardless, it MUST
    be in GMT (Greenwich Mean Time). It will be reformatted into an
    RFC 1123-compliant date and used as the value of an If-Modified-Since
    request header.

    If the agent argument is supplied, it will be used as the value of a
    User-Agent request header.

    If the referrer argument is supplied, it will be used as the value of a
    Referer[sic] request header.

    If handlers is supplied, it is a list of handlers used to build a
    urllib2 opener.

    if request_headers is supplied it is a dictionary of HTTP request headers
    that will override the values generated by FeedParser.

    :return: A bytes object.
    """

    if hasattr(url_file_stream_or_string, 'read'):
        return url_file_stream_or_string.read()

    if isinstance(url_file_stream_or_string, str) \
       and urllib.parse.urlparse(url_file_stream_or_string)[0] in ('http', 'https', 'ftp', 'file', 'feed'):
        return http.get(url_file_stream_or_string, etag, modified, agent, referrer, handlers, request_headers, result)

    # try to open with native open function (if url_file_stream_or_string is a filename)
    try:
        with open(url_file_stream_or_string, 'rb') as f:
            data = f.read()
    except (IOError, UnicodeEncodeError, TypeError, ValueError):
        # if url_file_stream_or_string is a str object that
        # cannot be converted to the encoding returned by
        # sys.getfilesystemencoding(), a UnicodeEncodeError
        # will be thrown
        # If url_file_stream_or_string is a string that contains NULL
        # (such as an XML document encoded in UTF-32), TypeError will
        # be thrown.
        pass
    else:
        return data

    # treat url_file_stream_or_string as string
    if not isinstance(url_file_stream_or_string, bytes):
        return url_file_stream_or_string.encode('utf-8')
    return url_file_stream_or_string


LooseFeedParser = type(
    'LooseFeedParser',
    (LooseXMLParser, XMLParserMixin, BaseHTMLProcessor),
    {},
)

StrictFeedParser = type(
    'StrictFeedParser',
    (StrictXMLParser, XMLParserMixin, xml.sax.handler.ContentHandler),
    {},
)


def parse(
        url_file_stream_or_string,
        etag: str = None,
        modified: Union[str, datetime.datetime, time.struct_time] = None,
        agent: str = None,
        referrer: str = None,
        handlers: List = None,
        request_headers: Dict[str, str] = None,
        response_headers: Dict[str, str] = None,
        resolve_relative_uris: bool = None,
        sanitize_html: bool = None,
) -> FeedParserDict:
    """Parse a feed from a URL, file, stream, or string.

    :param url_file_stream_or_string:
        File-like object, URL, file path, or string. Both byte and text strings
        are accepted. If necessary, encoding will be derived from the response
        headers or automatically detected.

        Note that strings may trigger network I/O or filesystem access
        depending on the value. Wrap an untrusted string in
        a :class:`io.StringIO` or :class:`io.BytesIO` to avoid this. Do not
        pass untrusted strings to this function.

        When a URL is not passed the feed location to use in relative URL
        resolution should be passed in the ``Content-Location`` response header
        (see ``response_headers`` below).
    :param etag:
        HTTP ``ETag`` request header.
    :param modified:
        HTTP ``Last-Modified`` request header.
    :param agent:
        HTTP ``User-Agent`` request header, which defaults to
        the value of :data:`feedparser.USER_AGENT`.
    :param referrer:
        HTTP ``Referer`` [sic] request header.
    :param handlers:
        A list of handlers that will be passed to urllib2.
    :param request_headers:
        A mapping of HTTP header name to HTTP header value to add to the
        request, overriding internally generated values.
    :param response_headers:
        A mapping of HTTP header name to HTTP header value. Multiple values may
        be joined with a comma. If a HTTP request was made, these headers
        override any matching headers in the response. Otherwise this specifies
        the entirety of the response headers.
    :param resolve_relative_uris:
        Should feedparser attempt to resolve relative URIs absolute ones within
        HTML content?  Defaults to the value of
        :data:`feedparser.RESOLVE_RELATIVE_URIS`, which is ``True``.
    :param sanitize_html:
        Should feedparser skip HTML sanitization? Only disable this if you know
        what you are doing!  Defaults to the value of
        :data:`feedparser.SANITIZE_HTML`, which is ``True``.

    """

    # Avoid a cyclic import.
    if not agent:
        import feedparser
        agent = feedparser.USER_AGENT
    if sanitize_html is None:
        import feedparser
        sanitize_html = feedparser.SANITIZE_HTML
    if resolve_relative_uris is None:
        import feedparser
        resolve_relative_uris = feedparser.RESOLVE_RELATIVE_URIS

    result = FeedParserDict(
        bozo=False,
        entries=[],
        feed=FeedParserDict(),
        headers={},
    )

    data = _open_resource(url_file_stream_or_string, etag, modified, agent, referrer, handlers, request_headers, result)

    if not data:
        return result

    # overwrite existing headers using response_headers
    result['headers'].update(response_headers or {})

    data = convert_to_utf8(result['headers'], data, result)
    use_json_parser = result['content-type'] == 'application/json'
    use_strict_parser = result['encoding'] and True or False

    if not use_json_parser:
        result['version'], data, entities = replace_doctype(data)

    # Ensure that baseuri is an absolute URI using an acceptable URI scheme.
    contentloc = result['headers'].get('content-location', '')
    href = result.get('href', '')
    baseuri = make_safe_absolute_uri(href, contentloc) or make_safe_absolute_uri(contentloc) or href

    baselang = result['headers'].get('content-language', None)
    if isinstance(baselang, bytes) and baselang is not None:
        baselang = baselang.decode('utf-8', 'ignore')

    if not _XML_AVAILABLE:
        use_strict_parser = 0
    if use_json_parser:
        result['version'] = None
        feed_parser = JSONParser(baseuri, baselang, 'utf-8')
        try:
            feed_parser.feed(data)
        except Exception as e:
            result['bozo'] = 1
            result['bozo_exception'] = e
    elif use_strict_parser:
        # Initialize the SAX parser.
        feed_parser = StrictFeedParser(baseuri, baselang, 'utf-8')
        feed_parser.resolve_relative_uris = resolve_relative_uris
        feed_parser.sanitize_html = sanitize_html
        saxparser = xml.sax.make_parser(PREFERRED_XML_PARSERS)
        saxparser.setFeature(xml.sax.handler.feature_namespaces, 1)
        try:
            # Disable downloading external doctype references, if possible.
            saxparser.setFeature(xml.sax.handler.feature_external_ges, 0)
        except xml.sax.SAXNotSupportedException:
            pass
        saxparser.setContentHandler(feed_parser)
        saxparser.setErrorHandler(feed_parser)
        source = xml.sax.xmlreader.InputSource()
        source.setByteStream(io.BytesIO(data))
        try:
            saxparser.parse(source)
        except xml.sax.SAXException as e:
            result['bozo'] = 1
            result['bozo_exception'] = feed_parser.exc or e
            use_strict_parser = 0

    # The loose XML parser will be tried if the JSON parser was not used,
    # and if the strict XML parser was not used (or if if it failed).
    if not use_json_parser and not use_strict_parser:
        feed_parser = LooseFeedParser(baseuri, baselang, 'utf-8', entities)
        feed_parser.resolve_relative_uris = resolve_relative_uris
        feed_parser.sanitize_html = sanitize_html
        feed_parser.feed(data.decode('utf-8', 'replace'))

    result['feed'] = feed_parser.feeddata
    result['entries'] = feed_parser.entries
    result['version'] = result['version'] or feed_parser.version
    result['namespaces'] = feed_parser.namespaces_in_use
    return result
