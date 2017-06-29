#
# Oxford Inference Compilation
# https://arxiv.org/abs/1610.09900
#
# Atilim Gunes Baydin, Tuan Anh Le, Mario Lezcano Casado, Frank Wood
# University of Oxford
# May 2016 -- June 2017
#

import infcomp
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import time
import datetime
import logging
import sys
import re
import os
from glob import glob
from termcolor import colored
from pprint import pformat
import cpuinfo
import locale

epsilon = 1e-8
beta_res = 1000
beta_step = 1/beta_res
ansi_escape = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')

def get_time_stamp():
    return datetime.datetime.fromtimestamp(time.time()).strftime('-%Y%m%d-%H%M%S')

def get_time_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def init(opt, mode=''):
    global Tensor
    global beta_integration_domain
    beta_integration_domain = Variable(torch.linspace(beta_step/2,1-(beta_step/2),beta_res), requires_grad=False)

    torch.manual_seed(opt.seed)
    if torch.cuda.is_available() and opt.cuda:
        torch.cuda.set_device(opt.device)
        torch.cuda.manual_seed(opt.seed)
        torch.backends.cudnn.enabled = True
        Tensor = torch.cuda.FloatTensor

        beta_integration_domain = beta_integration_domain.cuda()
    else:
        Tensor = torch.FloatTensor
        opt.cuda = False

    log_print()
    log_print(colored('[] Oxford Inference Compilation ' + infcomp.__version__, 'blue', attrs=['bold']))
    log_print()
    log_print(mode)
    log_print()

    log_print('Started ' + get_time_str())
    log_print()
    log_print('Running on PyTorch ' + torch.__version__)
    log_print()
    log_print('Command line arguments:')
    log_print(' '.join(sys.argv[1:]))

    log_print()
    log_print(colored('[] Hardware', 'blue', attrs=['bold']))
    log_print()
    if opt.cuda:
        log_print('Running on    : CUDA')
    else:
        log_print('Running on    : CPU')
    cpu_info = cpuinfo.get_cpu_info()
    if 'brand' in cpu_info:
        log_print('CPU           : {0}'.format(cpu_info['brand']))
    else:
        log_print('CPU           : unknown')
    if 'count' in cpu_info:
        log_print('CPU count     : {0}'.format(cpu_info['count']))
    else:
        log_print('CPU count     : unknown')
    if torch.cuda.is_available():
        log_print('CUDA          : available')
        log_print('CUDA devices  : {0}'.format(torch.cuda.device_count()))
        if opt.cuda:
            if opt.device == -1:
                log_print('CUDA selected : all')
            else:
                log_print('CUDA selected : {0}'.format(opt.device))
    else:
        log_print('CUDA          : not available')

    log_print()
    log_print(colored('[] Configuration', 'blue', attrs=['bold']))
    log_print()
    log_print(pformat(vars(opt)))
    log_print()

    if opt.visdom:
        try:
            import visdom
            global vis
            vis = visdom.Visdom()
        except:
            log_warning('Visdom server not available, disabling')
            opt.visdom = False

def init_logger(file_name):
    global logger
    logger = logging.getLogger()
    logger_file_handler = logging.FileHandler(file_name)
    logger_file_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(logger_file_handler)
    logger.setLevel(logging.INFO)

def remove_non_ascii(s):
    s = ansi_escape.sub('', s)
    return ''.join(i for i in s if ord(i)<128)

def log_print(line=''):
    print(line)
    logger.info(remove_non_ascii(line))

def log_error(line):
    print(colored('Error: ' + line, 'red', attrs=['bold']))
    logger.error(remove_non_ascii('Error: ' + line))
    quit()

def log_warning(line):
    print(colored('Warning: ' + line, 'red', attrs=['bold']))
    logger.warning(remove_non_ascii('Warning: ' + line))

def load_artifact(file_name, cuda=False, device_id=-1, print_info=True):
    try:
        if cuda:
            artifact = torch.load(file_name)
        else:
            artifact = torch.load(file_name, map_location=lambda storage, loc: storage)
    except:
        log_error('load_artifact: Cannot load file')
    if artifact.code_version != infcomp.__version__:
        log_print()
        log_warning('Different code versions (artifact: {0}, current: {1})'.format(artifact.code_version, infcomp.__version__))
        log_print()
    if artifact.pytorch_version != torch.__version__:
        log_print()
        log_warning('Different PyTorch versions (artifact: {0}, current: {1})'.format(artifact.pytorch_version, torch.__version__))
        log_print()

    if print_info:
        file_size = '{:,}'.format(os.path.getsize(file_name))
        log_print('File name             : {0}'.format(file_name))
        log_print('File size (Bytes)     : {0}'.format(file_size))
        log_print(artifact.get_info())
        log_print()

    if cuda:
        if device_id == -1:
            device_id = torch.cuda.current_device()

        if artifact.on_cuda:
            if device_id != artifact.cuda_device_id:
                log_warning('Loading CUDA (device {0}) artifact to CUDA (device {1})'.format(artifact.cuda_device_id, device_id))
                log_print()
                artifact.move_to_cuda(device_id)
        else:
            log_warning('Loading CPU artifact to CUDA (device {0})'.format(device_id))
            log_print()
            artifact.move_to_cuda(device_id)
    else:
        if artifact.on_cuda:
            log_warning('Loading CUDA artifact to CPU')
            log_print()
            artifact.move_to_cpu()

    return artifact

def get_trace_lengths(batch):
    ret = []
    for trace in batch:
        ret.append(trace.length)
    return ret

def standardize(t):
    mean = torch.mean(t)
    sd = torch.std(t)
    t.add_(-mean)
    t.div_(sd + epsilon)
    return t

def days_hours_mins_secs(total_seconds):
    d, r = divmod(total_seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return '{0}d:{1:02}:{2:02}:{3:02}'.format(int(d), int(h), int(m), int(s))

def file_starting_with(pattern, n):
    try:
        ret = sorted(glob(pattern + '*'))[n]
    except:
        log_error('Cannot find file')
    return ret

def truncate_str(s, length=80):
    return (s[:length] + '...') if len(s) > length else s

class Spinner(object):
    def __init__(self):
        self.i = 0
        self.spinner = [colored('|  \r', 'blue', attrs=['bold']),
                        colored(' | \r', 'blue', attrs=['bold']),
                        colored('  |\r', 'blue', attrs=['bold']),
                        colored(' | \r', 'blue', attrs=['bold'])]
    def spin(self):
        sys.stdout.write(self.spinner[self.i])
        sys.stdout.flush()
        self.i +=1
        if self.i > 3:
            self.i = 0

def pack_repetitions(l):
    if len(l) == 1:
        return [(l[0], 1)]
    else:
        ret = []
        prev = l[0]
        prev_count = 1
        for i in range(1,len(l)):
            if l[i] == prev:
                prev_count += 1
            else:
                ret.append((prev, prev_count))
                prev = l[i]
                prev_count = 1
        ret.append((prev, prev_count))
        return ret

def rgb_blend(rgb1,rgb2,blend):
    return rgb1[0] + (rgb2[0]-rgb1[0])*blend, rgb1[1] + (rgb2[1]-rgb1[1])*blend, rgb1[2] + (rgb2[2]-rgb1[2])*blend

def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(int(rgb[0]*255),int(rgb[1]*255),int(rgb[2]*255))

def beta(a, b):
    n = a.nelement()
    assert b.nelement() == n, 'a and b must have the same number of elements'
    a_min_one = (a-1).repeat(beta_res,1).t()
    b_min_one = (b-1).repeat(beta_res,1).t()
    x = beta_integration_domain.repeat(n,1)
    fx = (x**a_min_one) * ((1-x)**b_min_one)
    return torch.sum(fx,1).squeeze() * beta_step

def logsumexp(x, dim=0):
    '''
    https://en.wikipedia.org/wiki/LogSumExp
    input:
        x: Tensor/Variable [dim_1 * dim_2 * ... * dim_N]
        dim: n
    output: Tensor/Variable [dim_1 * ... * dim_{n - 1} * 1 * dim_{n + 1} * ... * dim_N]
    '''

    x_max, _ = x.max(dim)
    x_diff = x - x_max.expand_as(x)

    return x_max + x_diff.exp().sum(dim).log()
