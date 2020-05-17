# https://github.com/juho-lee/set_transformer/blob/master/modules.py

import torch
import torch.nn as nn
import torch.nn.functional as F
import math


class MAB(nn.Module):
    EPS = 1e-7

    def __init__(self, dim_Q, dim_K, dim_V, num_heads, ln=False):
        super(MAB, self).__init__()
        self.dim_V = dim_V
        self.num_heads = num_heads
        self.fc_q = nn.Linear(dim_Q, dim_V)
        self.fc_k = nn.Linear(dim_K, dim_V)
        self.fc_v = nn.Linear(dim_K, dim_V)
        if ln:
            self.ln0 = nn.LayerNorm(dim_V)
            self.ln1 = nn.LayerNorm(dim_V)
        self.fc_o = nn.Linear(dim_V, dim_V)

    def forward(self, Q, K, weights=None):
        Q = self.fc_q(Q)
        K, V = self.fc_k(K), self.fc_v(K)

        dim_split = self.dim_V // self.num_heads
        Q_ = torch.cat(Q.split(dim_split, 2), 0)
        K_ = torch.cat(K.split(dim_split, 2), 0)
        V_ = torch.cat(V.split(dim_split, 2), 0)

        A = self._compute_attention_weights(Q_, K_, weights)
        split_size = Q.shape[0]  # funky split spaghetti to avoid pytorch call overload issues
        if not isinstance(split_size, torch.Tensor):
            split_size = torch.IntTensor([split_size])[0]
        # split will still throw a tracing warning, but we can't avoid the int conversion...
        O = torch.cat(super(torch.Tensor, Q_ + A.bmm(V_)).split(split_size, dim=0), 2)
        O = O if getattr(self, "ln0", None) is None else self.ln0(O)
        O = O + F.relu(self.fc_o(O))
        O = O if getattr(self, "ln1", None) is None else self.ln1(O)
        return O

    def _compute_attention_weights(self, Q_, K_, weights=None):
        if weights is None:
            # Simple codepath for unweighted attention
            A = torch.softmax(Q_.bmm(K_.transpose(1, 2)) / math.sqrt(self.dim_V), 2)
        else:
            # If weights is a tensor, broadcast along all heads
            if torch.is_tensor(weights):
                weights = [weights] * self.num_heads
            assert isinstance(weights, list) and len(weights) == self.num_heads
            weights = torch.cat(weights, dim=0)
            assert weights.shape[0] == Q_.shape[0]
            # Log and clamp weights
            log_weights = torch.log(weights.clamp_min(0.) + self.EPS)
            attention_scores = Q_.bmm(K_.transpose(1, 2)) / math.sqrt(self.dim_V)
            A = torch.softmax(attention_scores + log_weights, 2)
        return A


class SAB(nn.Module):
    def __init__(self, dim_in, dim_out, num_heads, ln=False):
        super(SAB, self).__init__()
        self.mab = MAB(dim_in, dim_in, dim_out, num_heads, ln=ln)

    def forward(self, X, weights=None):
        return self.mab(X, X, weights)


class ISAB(nn.Module):
    def __init__(self, dim_in, dim_out, num_heads, num_inds, ln=False):
        super(ISAB, self).__init__()
        self.I = nn.Parameter(torch.Tensor(1, num_inds, dim_out))
        nn.init.xavier_uniform_(self.I)
        self.mab0 = MAB(dim_out, dim_in, dim_out, num_heads, ln=ln)
        self.mab1 = MAB(dim_in, dim_out, dim_out, num_heads, ln=ln)

    def forward(self, X, weights=None):
        H = self.mab0(self.I.repeat(X.size(0), 1, 1), X, weights)
        return self.mab1(X, H, weights)


class PMA(nn.Module):
    def __init__(self, dim, num_heads, num_seeds, ln=False):
        super(PMA, self).__init__()
        self.S = nn.Parameter(torch.Tensor(1, num_seeds, dim))
        nn.init.xavier_uniform_(self.S)
        self.mab = MAB(dim, dim, dim, num_heads, ln=ln)

    def forward(self, X, weights=None):
        return self.mab(self.S.repeat(X.size(0), 1, 1), X, weights)
