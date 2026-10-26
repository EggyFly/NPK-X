#include <acl/algorithm/uniformly_sampled/decoder.h>
#include <acl/core/ansi_allocator.h>
#include <acl/core/compressed_clip.h>
#include <acl/core/interpolation_utils.h>

#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>

int main(int argc, char** argv)
{
    if (argc != 3)
    {
        std::fprintf(stderr, "usage: acl_decode <input.acl> <output.qvv.f32>\n");
        return 2;
    }

    std::FILE* input = std::fopen(argv[1], "rb");
    if (!input)
    {
        std::fprintf(stderr, "cannot open input\n");
        return 1;
    }
    std::fseek(input, 0, SEEK_END);
    long length = std::ftell(input);
    std::fseek(input, 0, SEEK_SET);
    if (length < 16)
    {
        std::fprintf(stderr, "input too small\n");
        std::fclose(input);
        return 1;
    }

    void* buffer = _aligned_malloc((size_t)length, 16);
    if (!buffer || std::fread(buffer, 1, (size_t)length, input) != (size_t)length)
    {
        std::fprintf(stderr, "cannot read input\n");
        std::fclose(input);
        return 1;
    }
    std::fclose(input);

    const acl::CompressedClip* clip = reinterpret_cast<const acl::CompressedClip*>(buffer);
    acl::ErrorResult validation = clip->is_valid(true);
    if (!validation.empty())
    {
        std::fprintf(stderr, "invalid clip: %s\n", validation.c_str());
        _aligned_free(buffer);
        return 1;
    }

    const acl::ClipHeader& header = acl::get_clip_header(*clip);
    std::fprintf(stderr, "bones=%u samples=%u rate=%.6f duration=%.6f\n",
                 (unsigned)header.num_bones, (unsigned)header.num_samples,
                 (double)header.sample_rate, (double)(header.num_samples / header.sample_rate));

    acl::ANSIAllocator allocator;
    acl::uniformly_sampled::DecompressionContext<acl::uniformly_sampled::DefaultDecompressionSettings> context(&allocator);
    context.initialize(*clip);

    std::FILE* output = std::fopen(argv[2], "wb");
    if (!output)
    {
        std::fprintf(stderr, "cannot open output\n");
        _aligned_free(buffer);
        return 1;
    }

    for (uint32_t sample = 0; sample < header.num_samples; ++sample)
    {
        float sample_time = header.num_samples > 1
            ? (float)sample / header.sample_rate
            : 0.0f;
        context.seek(sample_time, acl::SampleRoundingPolicy::None);
        for (uint16_t bone = 0; bone < header.num_bones; ++bone)
        {
            acl::Quat_32 rotation;
            acl::Vector4_32 translation;
            acl::Vector4_32 scale;
            context.decompress_bone(bone, &rotation, &translation, &scale);
            float values[10] = {
                acl::quat_get_x(rotation), acl::quat_get_y(rotation), acl::quat_get_z(rotation), acl::quat_get_w(rotation),
                acl::vector_get_x(translation), acl::vector_get_y(translation), acl::vector_get_z(translation),
                acl::vector_get_x(scale), acl::vector_get_y(scale), acl::vector_get_z(scale),
            };
            std::fwrite(values, sizeof(float), 10, output);
        }
    }

    std::fclose(output);
    std::fflush(nullptr);
    std::_Exit(0);
    _aligned_free(buffer);
    return 0;
}
