stdp_nn_pre_centered
####################


stdp_nn_pre_centered - Synapse type for spike-timing dependent plasticity, with nearest-neighbour spike pairing

Description
+++++++++++

stdp_nn_pre_centered_synapse is a connector to create synapses with spike
time dependent plasticity with the presynaptic-centered nearest-neighbour
spike pairing scheme, as described in [1]_.

Each presynaptic spike is taken into account in the STDP weight change rule
with the nearest preceding postsynaptic one and the nearest succeeding
postsynaptic one (instead of pairing with all spikes, like in stdp_synapse).
So, when a presynaptic spike occurs, it is accounted in the depression rule
with the nearest preceding postsynaptic one; and when a postsynaptic spike
occurs, it is accounted in the facilitation rule with all preceding
presynaptic spikes that were not earlier than the previous postsynaptic
spike. For a clear illustration of this scheme see fig. 7B in [2]_.

The pairs exactly coinciding (so that presynaptic_spike == postsynaptic_spike
+ dendritic_delay), leading to zero delta_t, are discarded. In this case the
concerned pre/postsynaptic spike is paired with the second latest preceding
post/presynaptic one (for example, pre=={10 ms; 20 ms} and post=={20 ms} will
result in a potentiation pair 20-to-10).

The implementation involves two additional variables - presynaptic and
postsynaptic traces [2]_. The presynaptic trace decays exponentially over
time with the time constant tau_plus, increases by 1 on a pre-spike
occurrence, and is reset to 0 on a post-spike occurrence. The postsynaptic
trace (implemented on the postsynaptic neuron side) decays with the time
constant tau_minus and increases to 1 on a post-spike occurrence.

.. figure:: https://raw.githubusercontent.com/nest/nestml/master/doc/fig/stdp-nearest-neighbour.png

   Figure 7 from Morrison, Diesmann and Gerstner

   Original caption:

   Phenomenological models of synaptic plasticity based on spike timing", Biological Cybernetics 98 (2008). "Examples of nearest neighbor spike pairing schemes for a pre-synaptic neuron j and a postsynaptic neuron i. In each case, the dark gray indicate which pairings contribute toward depression of a synapse, and light gray indicate which pairings contribute toward potentiation. **(a)** Symmetric interpretation: each presynaptic spike is paired with the last postsynaptic spike, and each postsynaptic spike is paired with the last presynaptic spike (Morrison et al. 2007). **(b)** Presynaptic centered interpretation: each presynaptic spike is paired with the last postsynaptic spike and the next postsynaptic spike (Izhikevich and Desai 2003; Burkitt et al. 2004: Model II). **(c)** Reduced symmetric interpretation: as in **(b)** but only for immediate pairings (Burkitt et al. 2004: Model IV, also implemented in hardware by Schemmel et al. 2006)


References
++++++++++

.. [1] Izhikevich E. M., Desai N. S. (2003) Relating STDP to BCM,
       Neural Comput. 15, 1511--1523

.. [2] Morrison A., Diesmann M., and Gerstner W. (2008) Phenomenological
       models of synaptic plasticity based on spike timing,
       Biol. Cybern. 98, 459--478



Parameters
++++++++++



.. csv-table::
    :header: "Name", "Physical unit", "Default value", "Description"
    :widths: auto

    
    "the_delay", "ms", "1ms", "!!! cannot have a variable called ""delay"""    
    "lambda", "real", "0.01", ""    
    "tau_tr_pre", "ms", "20ms", ""    
    "tau_tr_post", "ms", "20ms", ""    
    "alpha", "real", "1.0", ""    
    "mu_plus", "real", "1.0", ""    
    "mu_minus", "real", "1.0", ""    
    "Wmax", "real", "100.0", ""    
    "Wmin", "real", "0.0", ""



State variables
+++++++++++++++

.. csv-table::
    :header: "Name", "Physical unit", "Default value", "Description"
    :widths: auto

    
    "w", "real", "1", ""    
    "pre_trace", "real", "0.0", ""    
    "post_trace", "real", "0.0", ""
Source code
+++++++++++

.. code-block:: nestml

   synapse stdp_nn_pre_centered:
     state:
       w real = 1
       pre_trace real = 0.0
       post_trace real = 0.0
     end
     parameters:
       the_delay ms = 1ms # !!! cannot have a variable called "delay"
       lambda real = 0.01
       tau_tr_pre ms = 20ms
       tau_tr_post ms = 20ms
       alpha real = 1.0
       mu_plus real = 1.0
       mu_minus real = 1.0
       Wmax real = 100.0
       Wmin real = 0.0
     end
     equations:
       # nearest-neighbour trace of presynaptic neuron
       pre_trace'=-pre_trace / tau_tr_pre
       # nearest-neighbour trace of postsynaptic neuron
       post_trace'=-post_trace / tau_tr_post
     end

     input:
       pre_spikes nS <-spike
       post_spikes nS <-spike
     end

     output: spike

     onReceive(post_spikes):
       post_trace = 1
       # potentiate synapse
       w_ real = Wmax * (w / Wmax + (lambda * (1.0 - (w / Wmax)) ** mu_plus * pre_trace))
       w = min(Wmax,w_)
       pre_trace = 0
     end

     onReceive(pre_spikes):
       pre_trace += 1
       # depress synapse
       w_ real = Wmax * (w / Wmax - (alpha * lambda * (w / Wmax) ** mu_minus * post_trace))
       w = max(Wmin,w_)
       # deliver spike to postsynaptic partner
       deliver_spike(w,the_delay)
     end

   end



Characterisation
++++++++++++++++

.. include:: stdp_nn_pre_centered_characterisation.rst


.. footer::

   Generated at 2021-12-09 08:22:33.070198
