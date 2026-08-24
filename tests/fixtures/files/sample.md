# Attention Is All You Need

## Introduction

We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.

## Method

The Transformer follows this overall architecture using stacked self-attention.

```python
def attention(Q, K, V):
    return softmax(Q @ K.T / sqrt(d)) @ V
```

## Results

On WMT 2014 English-to-German, the model achieves 28.4 BLEU.
