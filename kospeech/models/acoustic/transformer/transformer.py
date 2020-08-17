"""
Author:
    - **Soohwan Kim @sooftware**
    - **Email: sh951011@gmail.com**

Reference :
    - **https://github.com/graykode/nlp-tutorial**
    - **https://github.com/dreamgonfly/transformer-pytorch**
    - **https://github.com/jadore801120/attention-is-all-you-need-pytorch**
    - **https://github.com/JayParks/transformer**
"""
import torch.nn as nn
from torch import Tensor
from typing import (
    Optional,
    Tuple,
    Any
)
from kospeech.models.modules import (
    Linear,
    LayerNorm
)
from kospeech.models.acoustic.transformer.mask import (
    get_pad_mask,
    get_attn_pad_mask,
    get_decoder_self_attn_mask
)
from kospeech.models.acoustic.transformer.embeddings import (
    Embedding,
    PositionalEncoding
)
from kospeech.models.acoustic.transformer.layers import (
    SpeechTransformerEncoderLayer,
    SpeechTransformerDecoderLayer
)


class SpeechTransformer(nn.Module):
    """
    A Speech Transformer model. User is able to modify the attributes as needed.
    The architecture is based on the paper "Attention Is All You Need".

    Args:
        num_classes (int): the number of classfication
        d_model (int): dimension of model (default: 512)
        input_dim (int): dimension of input
        pad_id (int): identification of <PAD_token>
        eos_id (int): identification of <EOS_token>
        d_ff (int): dimension of feed forward network (default: 2048)
        num_encoder_layers (int): number of encoder layers (default: 6)
        num_decoder_layers (int): number of decoder layers (default: 6)
        num_heads (int): number of attention heads (default: 8)
        dropout_p (float): dropout probability (default: 0.3)
        ffnet_style (str): if poswise_ffnet is 'ff', position-wise feed forware network to be a feed forward,
            otherwise, position-wise feed forward network to be a convolution layer. (default: ff)

    Inputs: inputs, targets
        - **inputs** (batch, input_length): tensor containing input sequences
        - **targets** (batch, target_length): tensor contatining target sequences

    Returns: output
        - **output**: tensor containing the outputs
    """
    def __init__(
            self,
            num_classes: int,                      # the number of classfication
            d_model: int = 512,                    # dimension of model
            input_dim: int = 80,                   # dimension of input
            pad_id: int = 0,                       # identification of <PAD_token>
            eos_id: int = 2,                       # identification of <EOS_token>
            d_ff: int = 2048,                      # dimension of feed forward network
            num_heads: int = 8,                    # number of attention heads
            num_encoder_layers: int = 6,           # number of encoder layers
            num_decoder_layers: int = 6,           # number of decoder layers
            dropout_p: float = 0.3,                # dropout probability
            ffnet_style: str = 'ff'                # feed forward network style 'ff' or 'conv'
    ) -> None:
        super(SpeechTransformer, self).__init__()

        assert d_model % num_heads == 0, "d_model % num_heads should be zero."

        self.eos_id = eos_id
        self.pad_id = pad_id
        self.encoder = SpeechTransformerEncoder(
            d_model=d_model,
            input_dim=input_dim,
            d_ff=d_ff,
            num_layers=num_encoder_layers,
            num_heads=num_heads,
            ffnet_style=ffnet_style,
            dropout_p=dropout_p,
            pad_id=pad_id
        )
        self.decoder = SpeechTransformerDecoder(
            num_classes=num_classes,
            d_model=d_model,
            d_ff=d_ff,
            num_layers=num_decoder_layers,
            num_heads=num_heads,
            ffnet_style=ffnet_style,
            dropout_p=dropout_p,
            pad_id=pad_id,
            eos_id=eos_id
        )
        self.generator = Linear(d_model, num_classes)

    def forward(self, inputs: Tensor, input_lengths: Tensor,
                targets: Optional[Tensor] = None,
                return_attns: bool = False):
        """
        Args:
            inputs: B x T_input x D_Feature
            input_lengths: B x 1
            targets: B x T_output
            return_attns: bool
        """
        memory, encoder_self_attns = self.encoder(inputs, input_lengths)
        output, decoder_self_attns, memory_attns = self.decoder(targets, input_lengths, memory)
        output = self.generator(output)

        if return_attns:
            output = (output, encoder_self_attns, decoder_self_attns, memory_attns)
        else:
            del encoder_self_attns, decoder_self_attns, memory_attns

        return output


class SpeechTransformerEncoder(nn.Module):
    """
    The TransformerEncoder is composed of a stack of N identical layers.
    Each layer has two sub-layers. The first is a multi-head self-attention mechanism,
    and the second is a simple, position-wise fully connected feed-forward network.

    Args:
        d_model: dimension of model (default: 512)
        input_dim: dimension of feature vector (default: 80)
        d_ff: dimension of feed forward network (default: 2048)
        num_layers: number of encoder layers (default: 6)
        num_heads: number of attention heads (default: 8)
        ffnet_style: style of feed forward network [ff, conv] (default: ff)
        dropout_p:  probability of dropout (default: 0.3)
        pad_id: identification of pad token (default: 0)
    """
    def __init__(
            self,
            d_model: int = 512,         # dimension of model
            input_dim: int = 80,        # dimension of feature vector
            d_ff: int = 2048,           # dimension of feed forward network
            num_layers: int = 6,        # number of encoder layers
            num_heads: int = 8,         # number of attention heads
            ffnet_style: str = 'ff',    # style of feed forward network [ff, conv]
            dropout_p: float = 0.3,     # probability of dropout
            pad_id: int = 0             # identification of pad token
    ) -> None:
        super(SpeechTransformerEncoder, self).__init__()
        self.d_model = d_model
        self.num_layers = num_layers
        self.num_heads = num_heads
        self.pad_id = pad_id
        self.input_proj = Linear(input_dim, d_model)
        self.input_layer_norm = LayerNorm(d_model)
        self.input_dropout = nn.Dropout(p=dropout_p)
        self.positional_encoding = PositionalEncoding(d_model)
        self.layers = nn.ModuleList(
            [SpeechTransformerEncoderLayer(d_model, num_heads, d_ff, dropout_p, ffnet_style) for _ in range(num_layers)]
        )

    def forward(self, inputs: Tensor, input_lengths: Tensor = None):
        """
        Args:
            inputs: BxT_inputxD
            input_lengths: Bx1
        """
        self_attns = list()

        non_pad_mask = get_pad_mask(inputs, input_lengths=input_lengths).eq(False)
        self_attn_mask = get_attn_pad_mask(inputs, input_lengths, inputs.size(1))

        output = self.input_dropout(
            self.input_layer_norm(self.input_proj(inputs))
            + self.positional_encoding(inputs.size(1))
        )

        for layer in self.layers:
            output, attn = layer(output, non_pad_mask, self_attn_mask)
            self_attns.append(attn)

        return output, self_attns


class SpeechTransformerDecoder(nn.Module):
    """
    The TransformerDecoder is composed of a stack of N identical layers.
    Each layer has three sub-layers. The first is a multi-head self-attention mechanism,
    and the second is a multi-head attention mechanism, third is a feed-forward network.

    Args:
        num_classes: umber of classes
        d_model: dimension of model
        d_ff: dimension of feed forward network
        num_layers: number of decoder layers
        num_heads: number of attention heads
        ffnet_style: style of feed forward network
        dropout_p: probability of dropout
        pad_id: identification of pad token
        eos_id: identification of end of sentence token
    """
    def __init__(
            self,
            num_classes: int,               # number of classes
            d_model: int = 512,             # dimension of model
            d_ff: int = 512,                # dimension of feed forward network
            num_layers: int = 6,            # number of decoder layers
            num_heads: int = 8,             # number of attention heads
            ffnet_style: str = 'ff',        # style of feed forward network
            dropout_p: float = 0.3,         # probability of dropout
            pad_id: int = 0,                # identification of pad token
            eos_id: int = 2                 # identification of end of sentence token
    ) -> None:
        super(SpeechTransformerDecoder, self).__init__()
        self.d_model = d_model
        self.num_layers = num_layers
        self.num_heads = num_heads
        self.embedding = Embedding(num_classes, pad_id, d_model)
        self.positional_encoding = PositionalEncoding(d_model)
        self.input_dropout = nn.Dropout(p=dropout_p)
        self.layers = nn.ModuleList(
            [SpeechTransformerDecoderLayer(d_model, num_heads, d_ff,  dropout_p, ffnet_style) for _ in range(num_layers)]
        )
        self.pad_id = pad_id
        self.eos_id = eos_id

    def forward(self, inputs: Tensor, input_lengths: Optional[Any] = None, memory: Tensor = None):
        self_attns, memory_attns = list(), list()
        batch_size, output_length = inputs.size(0), inputs.size(1)
        inputs = inputs[inputs != self.eos_id].view(batch_size, -1)

        non_pad_mask = get_pad_mask(inputs, pad_id=self.pad_id).eq(False)
        self_attn_mask = get_decoder_self_attn_mask(inputs, inputs, self.pad_id)
        memory_mask = get_attn_pad_mask(memory, input_lengths, output_length)

        output = self.input_dropout(self.embedding(inputs) + self.positional_encoding(inputs.size(1)))

        for layer in self.layers:
            output, self_attn, memory_attn = layer(output, memory, non_pad_mask, self_attn_mask, memory_mask)
            self_attns.append(self_attn)
            memory_attns.append(memory_attn)

        return output, self_attns, memory_attns
