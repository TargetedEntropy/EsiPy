# -*- encoding: utf-8 -*-
""" App entry point. Uses Esi Meta Endpoint to work """
import time

import logging
import requests
from urllib.error import HTTPError

from openapi_core import OpenAPI

from .utils import check_cache
from .utils import get_cache_time_left
from .exceptions import APIException

LOGGER = logging.getLogger(__name__)


class OperationProxy:
    """Proxy object to make OpenAPI operations compatible with pyswagger interface"""
    
    def __init__(self, operation_id, openapi_wrapper):
        self.operation_id = operation_id
        self.openapi_wrapper = openapi_wrapper
        self.url = self._extract_url()
    
    def _extract_url(self):
        """Extract URL from the OpenAPI spec for this operation"""
        spec = self.openapi_wrapper.spec
        for path, methods in spec.get('paths', {}).items():
            for method, operation in methods.items():
                if operation.get('operationId') == self.operation_id:
                    # Return the path template
                    return path
        return None
    
    def __call__(self, **kwargs):
        """Create a request object compatible with EsiClient"""
        return OperationRequest(self.operation_id, self.openapi_wrapper, **kwargs)


class MockResponse:
    """Mock response object compatible with pyswagger interface"""
    
    def __init__(self):
        self.status = None
        self.header = {}
        self.data = None
        self.raw = None
        self.raw_body_only = False
    
    def reset(self):
        """Reset response for reuse"""
        self.status = None
        self.header = {}
        self.data = None
        self.raw = None
    
    def apply_with(self, status=None, header=None, raw=None):
        """Apply response data"""
        if status is not None:
            self.status = status
        if header is not None:
            self.header = header
        if raw is not None:
            self.raw = raw
            if not self.raw_body_only:
                import json
                try:
                    self.data = json.loads(raw.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.data = raw


class MockRequest:
    """Mock request object compatible with pyswagger interface"""
    
    def __init__(self, operation_id, openapi_wrapper, **params):
        self.operation_id = operation_id
        self.openapi_wrapper = openapi_wrapper
        self.params = params
        self.url = self._build_url()
        self.method = self._get_method()
        self._p = self._build_params()
        self.query = {}
        self.header = {}
        self.data = None
        
    def reset(self):
        """Reset request for reuse"""
        pass
    
    def prepare(self, scheme=None, handle_files=None):
        """Prepare request - compatibility method"""
        pass
    
    def _patch(self, opt):
        """Patch request with options - compatibility method"""
        pass
    
    def _get_method(self):
        """Get HTTP method for this operation"""
        spec = self.openapi_wrapper.spec
        for path, methods in spec.get('paths', {}).items():
            for method, operation in methods.items():
                if operation.get('operationId') == self.operation_id:
                    return method.upper()
        return 'GET'
    
    def _build_url(self):
        """Build the URL for this request"""
        spec = self.openapi_wrapper.spec
        
        # Extract the base URL from the OpenAPI spec
        servers = spec.get('servers', [])
        if servers:
            base_url = servers[0].get('url', 'https://esi.evetech.net')
        else:
            base_url = 'https://esi.evetech.net'
        
        base_url = base_url.rstrip('/')
        
        for path, methods in spec.get('paths', {}).items():
            for method, operation in methods.items():
                if operation.get('operationId') == self.operation_id:
                    # Replace path parameters
                    url_path = path
                    for param_name, param_value in self.params.items():
                        url_path = url_path.replace(f'{{{param_name}}}', str(param_value))
                    return base_url + url_path
        return base_url
    
    def _build_params(self):
        """Build parameters dict compatible with EsiClient"""
        return {
            'header': {},
            'path': {},
            'query': []
        }


class OperationRequest:
    """Request object compatible with EsiClient - returns tuple of (request, response)"""
    
    def __init__(self, operation_id, openapi_wrapper, **params):
        self.request = MockRequest(operation_id, openapi_wrapper, **params)
        self.response = MockResponse()
    
    def __iter__(self):
        """Make this object act like a tuple (request, response)"""
        return iter([self.request, self.response])
    
    def __getitem__(self, index):
        """Allow indexing like req_and_resp[0] and req_and_resp[1]"""
        if index == 0:
            return self.request
        elif index == 1:
            return self.response
        else:
            raise IndexError("Index out of range")


class OperationsCollection:
    """Collection of operations compatible with pyswagger app.op interface"""
    
    def __init__(self, openapi_wrapper):
        self.openapi_wrapper = openapi_wrapper
        self._operations = {}
        self._build_operations()
    
    def _build_operations(self):
        """Build operation proxies from OpenAPI spec"""
        spec = self.openapi_wrapper.spec
        for path, methods in spec.get('paths', {}).items():
            for method, operation in methods.items():
                operation_id = operation.get('operationId')
                if operation_id:
                    self._operations[operation_id] = OperationProxy(operation_id, self.openapi_wrapper)
    
    def __getitem__(self, key):
        return self._operations[key]
    
    def __contains__(self, key):
        return key in self._operations
    
    def values(self):
        return self._operations.values()
    
    def keys(self):
        return self._operations.keys()
    
    def items(self):
        return self._operations.items()


class OpenAPIWrapper:
    """Wrapper to make openapi-core compatible with pyswagger interface"""
    
    def __init__(self, spec, base_url):
        self.spec = spec
        self.base_url = base_url
        self.openapi = OpenAPI.from_dict(spec)
        self.op = OperationsCollection(self)


class EsiApp(object):
    """ EsiApp is an app object that'll allows us to play with ESI Meta
    API, not to have to deal with all ESI versions manually / meta """

    def __init__(self, **kwargs):
        """ Constructor.

        :param cache: if specified, use that cache, else use DictCache
        :param cache_time: is the minimum cache time for versions
            endpoints. If set to 0, never expires". None uses header expires
            Default 86400 (1d)
        :param cache_prefix: the prefix used to all cache key for esiapp
        :param meta_url: the meta url you want to use. Default is meta esi URL
            https://esi.evetech.net/swagger.json
        :param datasource: the EVE datasource to be used. Default: tranquility
        """
        self.meta_url = kwargs.pop(
            'meta_url',
            'https://esi.evetech.net/swagger.json'
        )
        self.expire = kwargs.pop('cache_time', 86400)
        if self.expire is not None and self.expire < 0:
            self.expire = 86400

        self.cache_prefix = kwargs.pop('cache_prefix', 'esipy')
        self.esi_meta_cache_key = '%s:app:meta_swagger_url' % self.cache_prefix

        cache = kwargs.pop('cache', False)
        self.caching = True if cache is not None else False
        self.cache = check_cache(cache)
        self.datasource = kwargs.pop('datasource', 'tranquility')

        self.app = self.__get_or_create_app(
            self.meta_url,
            self.esi_meta_cache_key
        )

    def __get_or_create_app(self, url, cache_key):
        """ Get the app from cache or generate a new one if required

        Because app object doesn't have etag/expiry, we have to make
        a head() call before, to have these informations first... """
        headers = {"Accept": "application/json"}
        app_url = '%s?datasource=%s' % (url, self.datasource)

        cached = self.cache.get(cache_key, (None, None, 0))
        if cached is None or len(cached) != 3:
            self.cache.invalidate(cache_key)
            cached_app, cached_headers, cached_expiry = (cached, None, 0)
        else:
            cached_app, cached_headers, cached_expiry = cached

        if cached_app is not None and cached_headers is not None:
            # we didn't set custom expire, use header expiry
            expires = cached_headers.get('expires', None)
            cache_timeout = -1
            if self.expire is None and expires is not None:
                cache_timeout = get_cache_time_left(
                    cached_headers['expires']
                )
                if cache_timeout >= 0:
                    return cached_app

            # we set custom expire, check this instead
            else:
                if self.expire == 0 or cached_expiry >= time.time():
                    return cached_app

            # if we have etags, add the header to use them
            etag = cached_headers.get('etag', None)
            if etag is not None:
                headers['If-None-Match'] = etag

            # if nothing makes us use the cache, invalidate it
            if ((expires is None or cache_timeout < 0 or
                 cached_expiry < time.time()) and etag is None):
                self.cache.invalidate(cache_key)

        # set timeout value in case we have to cache it later
        timeout = 0
        if self.expire is not None and self.expire > 0:
            timeout = time.time() + self.expire

        # we are here, we know we have to make a head call...
        res = requests.head(app_url, headers=headers)
        if self.expire is not None and self.expire > 0:
            expiration = self.expire
        else:
            expiration = get_cache_time_left(
                res.headers.get('expires')
            )
        if res.status_code == 304 and cached_app is not None:
            self.cache.set(
                cache_key,
                (cached_app, res.headers, timeout),
                expiration
            )
            return cached_app

        # ok, cache is not accurate, make the full stuff
        # also retry up to 3 times if we get any errors
        app = None
        for _retry in range(1, 4):
            try:
                # Download the OpenAPI spec
                spec_response = requests.get(app_url)
                spec_response.raise_for_status()
                
                # Create OpenAPI instance from the spec
                openapi_spec = spec_response.json()
                app = OpenAPIWrapper(openapi_spec, app_url)
            except (HTTPError, requests.RequestException) as error:
                LOGGER.warning(
                    "[failure #%d] %s: %r",
                    _retry,
                    app_url,
                    str(error)
                )
                continue
            break

        if app is None:
            raise APIException(
                app_url,
                500,
                response="Cannot fetch '%s'." % app_url
            )

        if self.caching and app:
            self.cache.set(cache_key, (app, res.headers, timeout), expiration)

        return app

    def __getattr__(self, name):
        """ Return the request object depending on its nature.

        if "op" is requested, simply return "app.op" which is a pyswagger app
        if anything else is requested, check if it exists, then if it's a
        swagger endpoint, try to create it and return it.
        """
        if name == 'op':
            return self.app.op

        try:
            op_attr = self.app.op[name]
        except KeyError:
            raise AttributeError('%s is not a valid operation' % name)

        # if the endpoint is a swagger spec
        if 'swagger.json' in op_attr.url:
            spec_url = 'https:%s' % op_attr.url
            cache_key = '%s:app:%s' % (self.cache_prefix, op_attr.url)
            return self.__get_or_create_app(spec_url, cache_key)
        else:
            raise AttributeError('%s is not a swagger endpoint' % name)

    def __getattribute__(self, name):
        """ Get attribute. If attribute is app, and app is None, create it
        again from cache / by querying ESI """
        attr = super(EsiApp, self).__getattribute__(name)
        if name == 'app' and attr is None:
            attr = self.__get_or_create_app(
                self.meta_url,
                self.esi_meta_cache_key
            )
            self.app = attr
        return attr

    def clear_cached_endpoints(self, prefix=None):
        """ Invalidate all cached endpoints, meta included

        Loop over all meta endpoints to generate all cache key the
        invalidate each of them. Doing it this way will prevent the
        app not finding keys as the user may change its prefixes
        Meta endpoint will be updated upon next call.
        :param: prefix the prefix for the cache key (default is cache_prefix)
        """
        prefix = prefix if prefix is not None else self.cache_prefix
        for endpoint in self.app.op.values():
            cache_key = '%s:app:%s' % (prefix, endpoint.url)
            self.cache.invalidate(cache_key)
        self.cache.invalidate('%s:app:meta_swagger_url' % self.cache_prefix)
        self.app = None
