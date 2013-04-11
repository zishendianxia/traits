""" Keeps a registry of available adaptation paths and handles adaptation. """


from heapq import heappop, heappush
import inspect
import itertools

from traits.api import HasTraits, Instance, Interface, List


class AdaptationError(TypeError):
    pass


class AdaptationManager(HasTraits):
    """ Keeps a registry of available adaptation paths and handles adaptation.
    """

    #### 'AdaptationManager' class protocol ###################################

    @staticmethod
    def mro_distance_to_protocol(from_type, to_protocol):
        """ If `from_type` provides `to_protocol`, returns the distance between
        `from_type` and the super-most class in the MRO hierarchy providing
        `to_protocol` (that's where the protocol was provided in the first
        place).

        If `from_type` does not provide `to_protocol`, return None.
        """

        if not AdaptationManager.provides_protocol(from_type ,to_protocol):
            return None

        # We walk up the MRO hierarchy until the point where the `to_protocol`
        # is no longer provided. That's where the protocol was provided in
        # the first place (e.g., the first super-class implementing an
        # interface).
        distance = 0
        supertypes = inspect.getmro(from_type)[1:]
        for t in supertypes:
            if not AdaptationManager.provides_protocol(t, to_protocol):
                break
            distance += 1

        return distance

    @staticmethod
    def provides_protocol(type_, protocol):
        """ Does object implement a given protocol?

        'protocol' is either an Interface or a class.

        Return True if the object implements the interface, or is an instance
        of the class.
        """

        if issubclass(protocol, Interface):
            # support for traits' Interfaces
            if hasattr(type_, '__implements__'):
                provides_protocol = issubclass(type_.__implements__, protocol)
            else:
                provides_protocol = False

        else:
            # 'protocol' is a class
            provides_protocol = issubclass(type_, protocol)

        return provides_protocol

    #### Private interface ####################################################

    #: All registered adaptation offers.
    _adaptation_offers = List(Instance('apptools.adaptation.adaptation_offer.AdaptationOffer'))

    #### Methods ##############################################################

    def adapt(self, adaptee, to_protocol, default=AdaptationError):
        """ Returns an adapter that adapts an object to a given protocol.

        `adaptee`     is the object that we want to adapt.
        `to_protocol` is the protocol that the adaptee should be adapted to.

        If `adaptee` already provides the given protocol then it is simply
        returned unchanged. Otherwise, we try to build a chain of adapters
        that adapt `adaptee` to `to_protocol`.

        If no such chain exists, an AdaptationError is raised unless a
        `default` return value is specified.

        """

        # If the object already provides the given protocol then it is
        # simply returned.
        if self.provides_protocol(type(adaptee), to_protocol):
            result = adaptee

        # Otherwise, try adapting the object.
        else:
            result = self._adapt(adaptee, to_protocol)

        if result is None:
            if default is AdaptationError:
                raise AdaptationError
            else:
                result = default

        return result

    def register_adaptation_offer(self, offer):
        """ Register an adaptation offer. """

        self._adaptation_offers.append(offer)

        return

    def register_adapter_factory(self, factory, from_protocol, to_protocol):
        """ Register an adapter factory.

        This is a convenience method that creates an AdaptationOffer instance
        from the given arguments and registers it.

        """

        from apptools.adaptation.adaptation_offer import AdaptationOffer

        offer = AdaptationOffer(
            factory       = factory,
            from_protocol = from_protocol,
            to_protocol   = to_protocol
        )

        self.register_adaptation_offer(offer)

        return

    def supports_protocol(self, obj, protocol):
        """ Does object support a given protocol?

        An object "supports" a protocol if either it "provides" it, or if
        can be adapted to it.

        """

        return self.adapt(obj, protocol, None) is not None

    #### Private protocol #####################################################

    _SUBCLASS_WEIGHT = 1e-9

    def _adapt(self, adaptee, to_protocol):
        """ Returns an adapter that adapts an object to the target class.

        Returns None if no such adapter exists.

        """

        # `offer_queue` is a priority queue. The values in the queue are
        # tuples (adapter, offer). `offer` is the adaptation offer used to get
        # from `adaptee` to `adapter` along the chain.
        # The priority in the priority queue is a tuple: the first element is
        # the number of steps that it took to go from `adaptee` to `adapter`.
        # The second number is the number of step the type hierarchy that we
        # need to take, so that more specific adapters are always preferred.

        # In other words, we are considering a weighted graph of all classes.
        # The adaptation path from `adaptee` to `to_protocol` is the shortest
        # weighted path in this graph.
        # The weights are 1 for each adapter we have to apply; parent and
        # child classes are connected with edges with a very small weight
        # (infinitesimally small).

        # Warning: The criterion for an outgoing edge being already visited
        # is that the adaptation offer (adapter factory, from, to protocol)
        # has been already used successfully once. In a very strange adaptation
        # graph, the application of an adaptation offer might lead to the
        # target protocol at a later point in time (e.g., if the adapters have
        # side effects on creation).
        # All the examples we considered for this case turn out to be
        # exceptionally bad designs of adapters, so we think these cases
        # can be safely regarded as irrelevant.

        # Unique sequence counter to make the priority list stable
        # w.r.t the sequence of insertion.
        counter = itertools.count()

        # The priority queue containing entries of the form
        # (cumulative weight, counter, object) describing the path
        # from `adaptee` to `adapter`.
        offer_queue = [((0, 0), counter.next(), adaptee)]

        # The set of visited adaptation offers.
        visited = set()

        while len(offer_queue) > 0:
            # Get the most specific candidate path for adaptation.
            path_weight, count, obj = heappop(offer_queue)

            edges = self._get_outgoing_edges(obj, visited)

            # Sort by weight first, then by from_protocol hierarchy.
            edges.sort(cmp=_cmp_weight_then_from_protocol_specificity)

            # At this point, the first edges are the shortest ones. Within
            # edges with the same distance, interfaces which are subclasses
            # of other interfaces in that group come first. The rest of
            # the order is unspecified.

            for mro_distance, offer in edges:
                adapter = offer.adapt(obj, offer.to_protocol)
                if adapter is not None:
                    visited.add(offer)

                    # Check if we arrived at the target protocol.
                    if self.provides_protocol(type(adapter), to_protocol):
                        return adapter

                    # Otherwise, push the new path on the priority queue.
                    path_adapter_weight, path_mro_weight = path_weight
                    total_weight = (path_adapter_weight + 1,
                                    path_mro_weight + mro_distance)
                    count = next(counter)
                    heappush(offer_queue, (total_weight, count, adapter))

        return None

    def _get_outgoing_edges(self, current_obj, visited):

        edges = []

        for offer in self._adaptation_offers:
            if offer in visited:
                continue

            # TODO: This method could be safely cached on each adaptation
            # attempt (NOT across adaptations), which could result in big
            # speed-ups for wide adaptation graphs.
            mro_distance = self.mro_distance_to_protocol(
                type(current_obj), offer.from_protocol
            )

            if mro_distance is not None:
                edges.append((mro_distance, offer))

        return edges


def _cmp_weight_then_from_protocol_specificity(edge_1, edge_2):
    # edge_1 and edge_2 are edges, of the form (mro_distance, offer)

    edge_1_mro_distance, edge_1_offer = edge_1
    edge_2_mro_distance, edge_2_offer = edge_2

    # First, compare the MRO distance.
    if edge_1_mro_distance < edge_2_mro_distance:
        return -1
    elif edge_1_mro_distance > edge_2_mro_distance:
        return 1

    # The distance is equal, prefer more specific 'from_protocol's
    if issubclass(edge_1_offer.from_protocol, edge_2_offer.from_protocol):
        return -1
    elif issubclass(edge_1_offer.from_protocol, edge_2_offer.from_protocol):
        return 1

    return 0


#: Default global adaptation manager.
adaptation_manager = AdaptationManager()


# Convenience functions acting on the default adaptation manager.

def adapt(adaptee, to_protocol, default=AdaptationError):

    return adaptation_manager.adapt(adaptee, to_protocol, default=default)


def register_adaptation_offer(offer):

    adaptation_manager.register_adaptation_offer(offer)

    return


def register_adapter_factory(factory, from_protocol, to_protocol):

    adaptation_manager.register_adapter_factory(
        factory, from_protocol, to_protocol
    )

    return


def supports_protocol(obj, protocol):

    return adaptation_manager.supports_protocol(obj, protocol)

#### EOF ######################################################################
