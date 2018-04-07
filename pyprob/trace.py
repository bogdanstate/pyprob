import torch
from . import util


class Sample(object):
    def __init__(self, distribution, value, address_base, address, instance, log_prob=None, control=False, replace=False, observed=False, reused=False, clamp_log_prob=True):
        self.address_base = address_base
        self.address = address
        self.distribution = distribution
        self.instance = instance
        self.value = util.to_variable(value)
        self.control = control
        self.replace = replace
        self.observed = observed
        self.reused = reused
        if log_prob is None:
            self.log_prob = distribution.log_prob(value)
        else:
            self.log_prob = util.to_variable(log_prob)
        if clamp_log_prob:
            self.log_prob = util.clamp_log_prob(self.log_prob)
        self.lstm_input = None
        self.lstm_output = None

    def __repr__(self):
        return 'Sample(control:{}, replace:{}, observed:{}, address:{}, distribution:{}, value:{})'.format(
            self.control,
            self.replace,
            self.observed,
            self.address,
            str(self.distribution),
            str(self.value)
        )

    def cuda(self, device=None):
        if self.value is not None:
            self.value = self.value.cuda(device)
        # self.distribution.cuda(device)

    def cpu(self):
        if self.value is not None:
            self.value = self.value.cpu()
        # self.distribution.cpu()


class Trace(object):
    def __init__(self):
        self.samples = []  # controlled
        self.samples_uncontrolled = []
        self.samples_observed = []
        self._samples_all = []
        self._samples_all_dict_address = {}
        self._samples_all_dict_adddress_base = {}
        self.result = None
        self.log_prob = 0.
        self.log_prob_observed = 0.
        self.log_importance_weight = 0.
        self.length = 0
        self._inference_network_training_observes_variable = None
        self._inference_network_training_observes_embedding = None

    def __repr__(self):
        return 'Trace(controlled:{}, uncontrolled:{}, observed:{}, log_prob:{})'.format(len(self.samples), len(self.samples_uncontrolled), len(self.samples_observed), float(self.log_prob))

    def addresses(self):
        return '; '.join([sample.address for sample in self.samples])

    def end(self, result):
        self.result = result
        self.samples = []
        replaced_indices = []
        for i in range(len(self._samples_all)):
            sample = self._samples_all[i]
            if sample.control and i not in replaced_indices:
                if sample.replace:
                    for j in range(i + 1, len(self._samples_all)):
                        if self._samples_all[j].address_base == sample.address_base:
                            sample = self._samples_all[j]
                            replaced_indices.append(j)
                self.samples.append(sample)
        self.samples_uncontrolled = [s for s in self._samples_all if (not s.control) and (not s.observed)]
        self.samples_observed = [s for s in self._samples_all if s.observed]
        self.log_prob_observed = util.to_variable(sum([torch.sum(s.log_prob) for s in self.samples_observed])).view(-1)

        self.log_prob = util.to_variable(sum([torch.sum(s.log_prob) for s in self._samples_all if s.control or s.observed])).view(-1)
        self._inference_network_training_observes_variable = util.pack_observes_to_variable([s.distribution.sample() for s in self.samples_observed])
        self.length = len(self.samples)

    def last_instance(self, address_base):
        if address_base in self._samples_all_dict_adddress_base:
            return self._samples_all_dict_adddress_base[address_base].instance
        else:
            return 0

    def add_sample(self, sample):
        self._samples_all.append(sample)
        self._samples_all_dict_address[sample.address] = sample
        self._samples_all_dict_adddress_base[sample.address_base] = sample

    def cuda(self, device=None):
        if self._inference_network_training_observes_variable is not None:
            self._inference_network_training_observes_variable = self._inference_network_training_observes_variable.cuda(device)
        if self._inference_network_training_observes_embedding is not None:
            self._inference_network_training_observes_embedding = self._inference_network_training_observes_embedding.cuda(device)
        for sample in self._samples_all:
            sample.cuda(device)

    def cpu(self):
        if self._inference_network_training_observes_variable is not None:
            self._inference_network_training_observes_variable = self._inference_network_training_observes_variable.cpu()
        if self._inference_network_training_observes_embedding is not None:
            self._inference_network_training_observes_embedding = self._inference_network_training_observes_embedding.cpu()
        for sample in self._samples_all:
            sample.cpu()
