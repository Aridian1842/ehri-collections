# Create your views here.

import re
from urllib import quote

from django.utils.translation import ugettext_lazy as _
from django.views.generic import ListView
from django import forms
from django.conf import settings

from haystack.forms import FacetedSearchForm
from haystack.query import SearchQuerySet

from ehriportal.portal import models

# Crime against programming
DATEMATH = re.compile("\[(?:(?P<from>\d{4})|(\*))[\s-].*?TO (?:(?P<to>\d{4})|(\*)).*?\]")


class FacetClass(object):
    """Class representing a facet with multiple values
    to filter on. i.e. keywords => [foo, bar ...]"""
    def __init__(self, name, prettyname, results):
        self.name = name
        self.prettyname = prettyname
        self.results = results
        self.facets = []

    def sorted_by_name(self):
        return sorted(self.facets, key=lambda k: k.name)

    def sorted_by_count(self):
        return sorted(self.facets, key=lambda k: -k.count)


class Facet(object):
    def __init__(self, klass, name, count, pretty=None, query=False):
        self.name = name
        self.klass = klass
        self.count = count
        self.query = query
        self.prettyname = pretty if pretty else name

    def filter_name(self):
        if self.query:
            return '%s:%s' % (self.klass.name, self.name)
        return '%s:"%s"' % (self.klass.name, self.name)

    def facet_param(self):
        return "sf=%s%%3A%s" % (quote(self.klass.name), quote(self.name))

    def is_selected(self):
        return self.filter_name() in self.klass.results.query.narrow_queries


def process_search_facets(sqs, facetnames):
    """Parse raw facet counts into a more managable set of FacetClass
    objects containing individual Facet object items."""
    counts = sqs.facet_counts()
    facetclasses = []

    # this is oh so gross at the moment. Now I have two problems...
    # This regexp matches query facets with the DATE pattern:
    # field:[<DATE> TO <DATE>]
    qfmatch = re.compile("(?P<fname>[^:]+):(?P<facet>" + DATEMATH.pattern + ")")

    if counts.get("queries"):
        qclasses = {}
        for facet, num in counts["queries"].iteritems():
            mf = qfmatch.match(facet)
            if not mf:
                raise ValueError("Query didn't match expected pattern: '%'" % facet)
            classname = mf.group("fname")
            fc = qclasses.get(classname)
            if not fc:
                fc = FacetClass(classname, classname.capitalize(), sqs)
                qclasses[classname] = fc
            facet = Facet(fc, mf.group("facet"), num, query=True)
            if mf.group("from") is None:
                facet.prettyname = "Before %s" % mf.group("to")
            elif mf.group("to") is None:
                facet.prettyname = "From %s" % mf.group("from")
            else:
                facet.prettyname = "%s-%s" % (mf.group("from"), mf.group("to"))
            fc.facets.append(facet)
        facetclasses.extend(qclasses.values())

    if counts.get("fields"):
        for key, pretty in facetnames.iteritems():
            flist = counts["fields"][key]
            facetclass = FacetClass(key, pretty, sqs)
            for item, count in flist:
                facetclass.facets.append(Facet(facetclass, item, count))
            facetclasses.append(facetclass)
    return facetclasses                


class SearchForm(forms.Form):
    q = forms.CharField(required=False, label=_('Search'))


class PortalSearchListView(ListView):
    model = None
    searchqueryset = None
    paginate_by = 20
    apply_facets = {}
    form_class = SearchForm

    def get_queryset(self):
        sqs = self.searchqueryset.models(self.model)
        for facet in self.apply_facets.keys():
            sqs = sqs.facet(facet)

        # apply the query
        self.form = self.form_class(self.request.GET)
        if self.form.is_valid():
            if self.form.cleaned_data["q"]:
                sqs = sqs.auto_query(self.form.cleaned_data["q"])

        # We need to process each facet to ensure that the field name and the
        # value are quoted correctly and separately:
        for facet in self.request.GET.getlist("sf"):
            if ":" not in facet:
                continue
            field, value = facet.split(":", 1)
            # FIXME: This part overrides the base class so that
            # facet values that match a date math string are NOT
            # quoted, which screws them up.  This is unfortunate 
            # and a better way needs to be found
            if value:
                keyval = u'%s:"%s"' % (field, sqs.query.clean(value))
                if DATEMATH.match(value):
                    keyval = u'%s:%s' % (field, value)
                sqs = sqs.narrow(keyval)
        self.searchqueryset = sqs
        return self.searchqueryset

    def get_context_data(self, *args, **kwargs):
        extra = super(PortalSearchListView, self).get_context_data(*args, **kwargs)
        # we need to process out facets in a way that makes it easy to
        # render them without too much horror in the template.
        extra["form"] = self.form
        extra["facet_classes"] = process_search_facets(
                self.searchqueryset, self.apply_facets)

        if getattr(settings, 'HAYSTACK_INCLUDE_SPELLING', False) and \
                self.form.is_valid():
            extra["suggestion"] = self.searchqueryset\
                    .spelling_suggestion(self.form.cleaned_data['q'])
        extra["querystring"] = self.request.META.get("QUERY_STRING", "")
        return extra


class FacetListSearchForm(SearchForm):
    sort = forms.ChoiceField(required=False, 
            choices=(("count","Count"), ("name", "Name")))

class PaginatedFacetView(PortalSearchListView):
    paginate_by = 10
    template_name = "portal/facets.html"
    template_name_ajax = "portal/_expanded_facet_list.html"

    def get_queryset(self):
        sqs = super(PaginatedFacetView, self).get_queryset()
        fclasses = process_search_facets(sqs, self.apply_facets)
        # look for the active facet
        print self.kwargs["facet"]
        try:
            fclass = [fc for fc in fclasses \
                    if fc.name == self.kwargs["facet"]][0]
        except IndexError:
            raise IndexError("Active class not found.")
        if self.form.cleaned_data["sort"] == "count":
            print "Sorting by count"
            return [f for f in fclass.sorted_by_count() if f.count]
        else:
            print "Sorting by name"
            return [f for f in fclass.sorted_by_name() if f.count]

    def get_template_name(self, **kwargs):
        if self.request.is_ajax():
            return [self.template_name_ajax]
        return [template_name]

    def get_context_data(self, **kwargs):
        extra = super(PaginatedFacetView, self).get_context_data(**kwargs)
        extra["facet_name"] = self.apply_facets[self.kwargs["facet"]]
        # hack! which tells us where to redirect to again
        extra["redirect"] = self.request.get_full_path()\
                .replace("/" + self.kwargs["facet"], "")
        return extra

